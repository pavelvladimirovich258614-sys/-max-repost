# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**max-repost** is a Telegram bot that transfers posts from Telegram channels to Max messenger (formerly VK Teams/ICQ). Users select a TG channel, a Max channel, and the number of posts to transfer. The bot downloads media from TG via Telethon (user session), uploads to Max API, and posts 1:1.

Business model: Paid transfers at 3 RUB/post (configurable via `PRICE_PER_POST`), with free trial posts per user (`FREE_POSTS_BONUS`, default 10). Admins bypass charges.

**Core Services:**
- Manual post transfer (bulk)
- Autoposting (automatic forwarding of new posts)
- YooKassa payment integration
- Promo code system for bonus posts
- Max bot listener (responds to messages in Max messenger)
- Admin commands (balance top-up)

## Development Commands

### Running the Bot
```bash
pip install -r requirements.txt
python -m bot.main
```

### Database
SQLite (`./data/bot.db`) is used (not PostgreSQL). Tables are auto-created on startup via `Base.metadata.create_all` in `main.py`. Simple column migrations run inline in `_run_column_migrations()` using SQLite pragma checks — **not** via Alembic (Alembic migrations exist but are not the active migration path).

### Telethon Authentication
Requires a Telethon **user session** (not bot token) to read TG channel history. Two formats:
1. **Session file** (`user_session.session`) in project root — local dev
2. **String session** (`TELETHON_SESSION_STRING` env var) — production (recommended)

```bash
python scripts/auth_telethon.py          # authorize/create session
python scripts/export_session.py         # export to string for production
```

### Debug Scripts
```bash
python scripts/get_max_chat_id.py        # get Max chat ID from channel link
python scripts/listen_updates.py         # debug Telethon updates
python scripts/inspect_posts.py @channel # inspect channel posts
python scripts/test_transfer.py          # test transfer manually
```

### Tests
No tests exist yet (`tests/` is empty).

## Architecture

### Startup Order (critical)
`main.py` starts services in this order: DB init → aiogram bot/dispatcher → MaxClient → YooKassa checker → webhook server → Max bot listener → **aiogram polling** (bot becomes responsive) → **Telethon** (background, non-blocking). If Telethon fails, bot stays responsive but transfer/autopost features are disabled.

### Telegram Bot (aiogram 3)

**FSM Storage**: `MemoryStorage` (in-memory, not Redis) — state is lost on bot restart.

**State Groups** (`bot/telegram/states.py`):
- `AutopostStates` — auto-repost setup flow
- `TransferStates` — manual transfer flow (TG channel → verify → Max channel → count)
- `ChannelStates` — channel management
- `AdminStates` — admin operations (balance top-up)

**Router Registration Order** (matters — more specific first):
1. `start_router`
2. `autopost_router`
3. `transfer_router`
4. `channels_router`
5. `payment_router`
6. `admin_router`

### DBMiddleware (handler data injection)
`bot/telegram/middlewares/db.py` creates a DB session per update and injects **both** the raw session and all repositories into handler `data` dict. Access in handlers:
```python
async def handler(message: Message, state: FSMContext, session: AsyncSession, user_repo: UserRepository):
```
Available keys: `session`/`db_session`, `user_repo`, `channel_repo`, `post_repo`, `payment_repo`, `promo_repo`, `promo_activation_repo`, `log_repo`, `verified_channel_repo`, `balance_repo`, `transaction_repo`, `autopost_sub_repo`, `max_binding_repo`, `yookassa_payment_repo`, `transferred_post_repo`.

### Core Components

**`bot/core/transfer_engine.py`** — Main transfer orchestration
- Fetches posts from TG via Telethon (oldest first, `reverse=True`)
- Downloads media to BytesIO, uploads to Max API
- `convert_entities_to_html()` — TG formatting entities → HTML for Max (handles UTF-16 offsets for emoji)
- `split_text()` — splits text >4000 chars into chunks
- Progress callbacks for UI updates
- Duplicate protection via `TransferredPostRepository`
- Aborts after 5 consecutive errors

**`bot/core/autopost.py`** — Automatic post forwarding
- **Polling-based** (not event handlers) — `POLL_INTERVAL = 10` seconds
- **Album buffering**: `ALBUM_BUFFER_WAIT = 2` seconds before forwarding grouped media
- **Catch-up logic**: processes missed posts when resuming after pause
- Low balance notifications (max once per day per user)
- Singleton pattern: `get_autopost_manager()` / `set_autopost_manager()`

**`bot/core/telethon_client.py`** — TG channel history via MTProto (requires user session)
- `count_channel_posts()` — efficient count without fetching messages
- `iter_messages()` — memory-efficient iteration

**`bot/core/content_filter.py`** — Spam/ad filtering for autoposted content
- `should_skip_post()` — filters ads, short text, forwarded posts, excessive links

**`bot/core/verification.py`** — Channel ownership verification flow

**`bot/max_api/client.py`** — Max Platform API client
- Rate-limited (token bucket, `MAX_RPS`, default 25)
- Two-step file upload: `POST /uploads` → upload to URL → get token
- Token location varies by media type:
  - **image/file**: token in step 2 response, nested under `{"photos": {"<hash>": {"token": "..."}}}`
  - **video/audio**: token in step 1 response

### Payment System

**`bot/payments/yookassa_client.py`** — YooKassa gateway with 54-FZ fiscal receipts
- `create_payment()` → `(payment_id, confirmation_url)`
- `check_payment()` → `"pending"` / `"succeeded"` / `"canceled"`

**`bot/payments/payment_checker.py`** — Background polling for pending payments, credits balance on success

**`bot/payments/webhook_server.py`** — aiohttp server for YooKassa notifications (optional fallback alongside polling)

**`bot/database/balance.py`** — Balance operations (charge per post, deposit, refund) with transaction logging

### Max Bot Listener

**`bot/max_api/max_bot_handler.py`** — Polls Max API for `bot_started` and `message_created` events, sends setup instructions. Required for Max channel setup flow.

### Database

**ORM**: SQLAlchemy 2.0 async with `NullPool` (required for SQLite async)

**SQLite config**: WAL mode, `synchronous=NORMAL`, 64MB cache, 30s lock timeout — all set in `connection.py` pragmas.

**Models** (`bot/database/models.py`):
- `User` — balance, `free_posts_used`, `is_admin`, `referral_code`, `referred_by`
- `UserBalance` — separate ruble-balance tracking with `total_deposited`/`total_spent`
- `BalanceTransaction` — transaction history (deposit, autopost_charge, admin_topup, refund)
- `Channel` — TG→Max bindings, `auto_repost` flag, `last_post_id`
- `Post` — tracking reposted content with status (pending/sent/failed)
- `TransferredPost` — duplicate prevention (unique per tg_channel + max_chat_id + msg_id)
- `MaxChannelBinding` — saved Max channels for quick reuse
- `VerifiedChannel` — verified TG channel ownership
- `AutopostSubscription` — autopost settings, stats, `paused_reason`, `cost_per_post`, `total_spent`
- `Payment` / `YooKassaPayment` — payment tracking (legacy and YooKassa-specific)
- `PromoCode` / `PromoActivation` — promo code system with activation limits
- `Log` — audit log with JSON details

**Repositories**: All in `bot/database/repositories/`. Extend `BaseRepository[T]` which provides generic CRUD. Always use repositories, never raw ORM in handlers.

## Configuration

**File**: `config/settings.py` (Pydantic Settings, loads from `.env`)

**Key .env variables**:
- `TELEGRAM_BOT_TOKEN` — BotFather token
- `ADMIN_TELEGRAM_ID` — Single admin ID (required)
- `ADMIN_IDS` — Comma-separated admin IDs (optional, for multi-admin)
- `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` — From https://my.telegram.org
- `TELEGRAM_PHONE` — User session phone
- `TELETHON_SESSION_STRING` — String session (alternative to .session file)
- `MAX_ACCESS_TOKEN` — Max Platform API token
- `DATABASE_URL` — Default: `sqlite+aiosqlite:///./data/bot.db`
- `SOCKS_PROXY` — SOCKS5 proxy for Telegram access (default: `socks5://127.0.0.1:1080`). Used by both aiogram and Telethon.
- `YOOKASSA_SHOP_ID` / `SECRET_KEY` — YooKassa credentials
- `RECEIPT_EMAIL` — Default email for 54-FZ receipts
- `PRICE_PER_POST` — Default 3 (RUB)
- `FREE_POSTS_BONUS` — Default 10 free posts
- `MAX_RPS` — Rate limit for Max API (default 25)
- `BONUS_CHANNEL` — Channel for bonus subscription verification
- `WEBHOOK_ENABLED` / `WEBHOOK_HOST` / `WEBHOOK_PORT` — YooKassa webhook server (default: `0.0.0.0:8080`)

## Important Notes

- **Telethon session must exist** before running bot. Run auth script if missing.
- **Max API tokens vary by media type** — check response structure carefully in `client.py`.
- **Duplicate protection** is enforced at DB level — don't bypass.
- **Rate limiting** is built into MaxClient — don't add extra delays around it.
- **FSM uses MemoryStorage** — state is lost on bot restart.
- **Text >4000 chars** is split into multiple Max messages.
- **Album posts** with `grouped_id` are handled but only first media is transferred (TODO: full album support).
- **Autoposting uses polling** (not event handlers) — more reliable for channels where user is author.
- **Private channels** require numeric channel ID, not username (stored in `tg_channel_id` column).
- **Attachment not ready** — Max API needs time to process uploads (up to 60s for large files). Use `_send_with_retry()` pattern.
- **Balance charging** — Autopost charges per post from `UserBalance`, admins bypass charge.
- **Graceful shutdown** — Bot handles SIGTERM/SIGINT, stops autopost tasks, closes clients properly. On Windows, signal handlers are not supported (targets Linux VPS).
