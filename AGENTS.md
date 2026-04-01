# AGENTS.md

This file provides essential information for AI coding agents working with the **max-repost** codebase.

## Project Overview

**max-repost** is a Telegram bot that transfers posts from Telegram channels to Max messenger (formerly VK Teams/ICQ). 

**Business Model:** Paid transfers at 3 RUB/post, with 5 free trial posts per new user.

**Core Features:**
- Manual bulk post transfer (TG → Max)
- Autoposting (automatic forwarding of new posts)
- YooKassa payment integration with 54-FZ receipts
- Max bot listener (responds to messages in Max messenger)
- Referral system with bonuses
- Promo code support

## Technology Stack

| Component | Technology |
|-----------|------------|
| Language | Python 3.11+ |
| Telegram Bot Framework | aiogram 3.25.0 |
| Database ORM | SQLAlchemy 2.0 (async) |
| Database | SQLite (aiosqlite) - PostgreSQL planned but not currently used |
| Migrations | Alembic 1.13.1 |
| TG Channel Access | Telethon 1.34.0 (MTProto API, requires user session) |
| Max API | Custom client (`bot/max_api/client.py`) |
| Payments | YooKassa 3.10.0 |
| Logging | Loguru 0.7.2 |
| Configuration | Pydantic Settings 2.1.0 |
| FSM Storage | MemoryStorage (in-memory, state lost on restart) |

## Project Structure

```
max-repost/
├── bot/                          # Main application code
│   ├── main.py                   # Entry point, orchestrates all services
│   ├── core/                     # Core business logic
│   │   ├── transfer_engine.py    # Main transfer orchestration (TG → Max)
│   │   ├── autopost.py           # Autopost manager (polling-based)
│   │   ├── telethon_client.py    # TG channel history reader
│   │   ├── text_formatter.py     # Entity conversion (TG → HTML)
│   │   ├── media_processor.py    # Media handling utilities
│   │   ├── rate_limiter.py       # Rate limiting utilities
│   │   ├── verification.py       # Channel ownership verification
│   │   └── content_filter.py     # Content filtering
│   ├── database/                 # Data layer
│   │   ├── models.py             # SQLAlchemy ORM models
│   │   ├── connection.py         # Async engine & session factory
│   │   └── repositories/         # Repository pattern implementations
│   │       ├── base.py
│   │       ├── user.py
│   │       ├── channel.py
│   │       ├── autopost_subscription.py
│   │       ├── transferred_post.py
│   │       ├── balance.py
│   │       └── ... (13 total)
│   ├── telegram/                 # aiogram handlers & UI
│   │   ├── bot.py                # Bot & dispatcher initialization
│   │   ├── states.py             # FSM state groups
│   │   ├── handlers/             # Message/command handlers
│   │   │   ├── start.py          # /start, /menu, /help
│   │   │   ├── transfer.py       # Manual transfer flow
│   │   │   ├── autopost.py       # Autopost setup
│   │   │   ├── channels.py       # Channel management
│   │   │   ├── payment.py        # Payment handlers
│   │   │   └── admin.py          # Admin commands
│   │   ├── keyboards/            # Inline/reply keyboards
│   │   └── middlewares/          # aiogram middlewares
│   │       └── db.py             # Database session injection
│   ├── max_api/                  # Max Platform integration
│   │   ├── client.py             # Rate-limited API client
│   │   └── max_bot_handler.py    # Max bot event listener
│   ├── payments/                 # Payment processing
│   │   ├── yookassa_client.py    # YooKassa API client
│   │   ├── payment_checker.py    # Background payment polling
│   │   └── webhook_server.py     # aiohttp webhook server
│   ├── services/                 # Business services layer
│   │   └── payment.py
│   └── utils/                    # Utilities
│       └── logger.py             # Loguru configuration
├── config/                       # Configuration
│   └── settings.py               # Pydantic Settings
├── alembic/                      # Database migrations
│   ├── versions/                 # Migration files
│   └── env.py
├── docker/                       # Docker configuration
│   ├── docker-compose.yml        # PostgreSQL + Redis services
│   ├── Dockerfile
│   └── max-repost.service        # systemd service file
├── scripts/                      # Development utilities
│   ├── auth_telethon.py          # Create Telethon session
│   ├── export_session.py         # Export session to string
│   ├── get_max_chat_id.py        # Get Max chat ID from link
│   ├── listen_updates.py         # Debug Telethon updates
│   ├── inspect_posts.py          # Inspect channel posts
│   └── test_transfer.py          # Test transfer manually
├── tests/                        # Test suite (minimal)
└── logs/                         # Log files (created at runtime)
```

## Development Commands

### Running the Bot

```bash
# Install dependencies
pip install -r requirements.txt

# Start the bot
python -m bot.main
```

### Database Operations

```bash
# Run migrations
alembic upgrade head

# Create new migration
alembic revision --autogenerate -m "description"
```

### Docker Services

```bash
cd docker
docker-compose up -d
```

### Telethon Authentication (Required Before First Run)

The bot requires a Telethon **user session** (not bot token) to read TG channel history.

**Two formats supported:**
1. **Session file** (`user_session.session`) - for local development
2. **String session** (`TELETHON_SESSION_STRING` env var) - for production

```bash
# Create/authenticate session
python scripts/auth_telethon.py

# Export to string for production
python scripts/export_session.py
```

### Development Scripts

```bash
# Get Max chat ID from channel link
python scripts/get_max_chat_id.py

# Listen to Telegram updates (debug)
python scripts/listen_updates.py

# Inspect channel posts
python scripts/inspect_posts.py @channel_name

# Test transfer manually
python scripts/test_transfer.py
```

## Architecture Details

### Startup Sequence (Critical)

The main.py uses a specific startup order to ensure bot responsiveness:

1. **Initialize database** (`init_db()`)
2. **Start aiogram polling** FIRST - bot becomes responsive immediately
3. **Initialize Telethon** in BACKGROUND - doesn't block polling
4. **Load autopost subscriptions** in background
5. **Start payment checker** background task
6. **Start Max bot listener** background task
7. **Start webhook server** (if enabled)

### Core Components

#### TransferEngine (`bot/core/transfer_engine.py`)

Main orchestration for post transfer:
- Fetches posts from TG via Telethon (oldest first)
- Downloads media to BytesIO, uploads to Max API
- Converts Telegram entities → HTML for Max
- Progress callbacks for UI updates
- Duplicate protection via `TransferredPostRepository`
- Aborts after 5 consecutive errors

**Key method:**
```python
result = await engine.transfer_posts(
    tg_channel="@channel",
    max_channel_id=12345,
    count=10,
    progress_callback=callback,
)
# Returns: success, failed, skipped, duplicates counts
```

#### MaxClient (`bot/max_api/client.py`)

Rate-limited Max Platform API client:
- Token bucket rate limiting (default 25 RPS)
- Two-step file upload: `POST /uploads` → upload to URL → get token
- Retry logic for 429 and 5xx errors
- Token location varies by media type:
  - **image/file**: token in step 2 response, nested under `{"photos": {"<hash>": {"token": "..."}}}`
  - **video/audio**: token in step 1 response

#### TelethonChannelClient (`bot/core/telethon_client.py`)

TG channel history reader:
- Uses MTProto API (requires user session, not bot)
- `count_channel_posts()` - efficient count without fetching messages
- `iter_messages()` - memory-efficient iteration with `reverse=True`

#### AutopostManager (`bot/core/autopost.py`)

Automatic post forwarding:
- **Polling-based** (not event handlers) for reliability with channel authorship
- `POLL_INTERVAL = 10` seconds for checking new messages
- **Album buffering**: Collects album parts for `ALBUM_BUFFER_WAIT = 2` seconds before forwarding
- **Catch-up logic**: Processes missed posts when autoposting resumes after pause
- Private channel support (uses numeric ID instead of username)
- Low balance notifications (max once per day per user)
- Singleton pattern via `get_autopost_manager()` / `set_autopost_manager()`

### Database Session in Handlers

Use middleware injection (do NOT create sessions manually in handlers):

```python
from bot.telegram.middlewares.db import DBMiddleware

@dp.message(Command("start"))
async def handler(message: Message, session: AsyncSession):
    repo = UserRepository(session)
    user = await repo.get_by_telegram_id(message.from_user.id)
```

### Repository Pattern

All database access goes through repositories in `bot/database/repositories/`:

```python
from bot.database.repositories.user import UserRepository
from bot.database.repositories.autopost_subscription import AutopostSubscriptionRepository

async with get_session() as session:
    user_repo = UserRepository(session)
    user = await user_repo.get_by_telegram_id(telegram_id)
```

**Never use direct ORM queries in handlers or services.**

### Text Formatting

Telegram entities are converted to HTML for Max API:

```python
from bot.core.transfer_engine import convert_entities_to_html

html_text = convert_entities_to_html(text, message.entities)
```

- Handles UTF-16 offsets for emoji compatibility
- Text >4000 chars is split into chunks via `split_text()`

### Payment Flow

```python
from bot.payments.yookassa_client import YooKassaClient

client = YooKassaClient()
payment_id, confirmation_url = await client.create_payment(
    user_id=telegram_id,
    amount_rub=Decimal("100.00"),
    description="Пополнение баланса",
)
# Send confirmation_url to user
```

Payment status is polled by `PaymentChecker` background task or via webhook.

## Configuration

**File:** `config/settings.py` (Pydantic Settings)

**Required Environment Variables:**

| Variable | Description |
|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | Bot token from @BotFather |
| `ADMIN_IDS` | Comma-separated admin Telegram IDs |
| `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` | From https://my.telegram.org |
| `TELEGRAM_PHONE` | Phone number for Telethon session |
| `TELETHON_SESSION_STRING` | String session (alternative to .session file) |
| `MAX_ACCESS_TOKEN` | Max Platform API token |
| `YOOKASSA_SHOP_ID` / `YOOKASSA_SECRET_KEY` | Payment credentials |
| `BONUS_CHANNEL` | Channel for subscription verification |

**Optional Variables:**

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `sqlite+aiosqlite:///./data/bot.db` | Database connection |
| `PRICE_PER_POST` | 3 | Price per post in RUB |
| `FREE_POSTS_BONUS` | 10 | Free posts for new users |
| `MAX_RPS` | 25 | Rate limit for Max API |
| `WEBHOOK_ENABLED` | true | Enable YooKassa webhook |
| `WEBHOOK_HOST`/`PORT` | 0.0.0.0:8080 | Webhook server binding |
| `SOCKS_PROXY` | `socks5://127.0.0.1:1080` | Proxy for Telegram |

## Database Models

**Key Models** (from `bot/database/models.py`):

| Model | Purpose |
|-------|---------|
| `User` | balance, free_posts_used (max 5), admin flag, referral_code |
| `Channel` | TG→Max bindings, auto_repost flag |
| `Post` | tracking reposted content |
| `TransferredPost` | **duplicate protection** (unique per TG channel + Max channel + msg_id) |
| `MaxChannelBinding` | saved Max channels for quick reuse |
| `VerifiedChannel` | verified TG channel ownership |
| `AutopostSubscription` | autopost settings (tg_channel, max_chat_id, last_post_id, pause_reason) |
| `Payment` / `YooKassaPayment` | payment transactions |
| `PromoCode` / `PromoActivation` | promo code system |
| `UserBalance` / `BalanceTransaction` | balance tracking in rubles |
| `Log` | audit log |

## FSM States

**State Groups** (`bot/telegram/states.py`):

- `AutopostStates` - Auto-repost setup flow
- `TransferStates` - Manual transfer flow (TG channel → verify → Max channel → count)
- `ChannelStates` - Channel management
- `AdminStates` - Admin operations

**Handler Registration Order** (matters - more specific first):
1. start_router
2. autopost_router
3. transfer_router
4. channels_router
5. payment_router
6. admin_router

## Code Style Guidelines

1. **Type Hints**: Use throughout (Python 3.11+ syntax)
2. **Async/Await**: All I/O operations are async
3. **Repository Pattern**: Never use direct ORM in handlers
4. **Logging**: Use `from loguru import logger` (not standard logging)
5. **Error Handling**: Use try/except with proper logging
6. **Docstrings**: Google-style docstrings for functions
7. **Constants**: UPPER_CASE for module-level constants

## Testing

**Current State:** Minimal test coverage (tests folder exists but nearly empty)

To add tests:
```bash
# Create test file
tests/test_feature.py

# Run tests
pytest
```

## Security Considerations

1. **Telethon Session**: Keep `user_session.session` and `TELETHON_SESSION_STRING` secure - they provide full Telegram account access
2. **YooKassa Keys**: Store securely, use environment variables
3. **Admin IDs**: Restrict sensitive commands to `ADMIN_IDS`
4. **Duplicate Protection**: Enforced at database level - don't bypass `TransferredPostRepository`
5. **Rate Limiting**: Built into MaxClient - don't add extra delays

## Important Notes

- **Telethon session must exist** before running bot. Run auth script if missing.
- **Max API tokens vary by media type** - check response structure carefully.
- **FSM uses MemoryStorage** - state is lost on bot restart.
- **Album posts** with grouped_id are handled but only first media is transferred (TODO: full album support).
- **Private channels** require numeric channel ID, not username.
- **Attachment not ready** - Max API needs time to process uploads (up to 60s for large files). Use `_send_with_retry()` pattern.
- **Balance charging** - Autopost charges per post (3 RUB = 3 posts from balance), admins bypass charge.
- **Graceful shutdown** - Bot handles SIGTERM/SIGINT, stops autopost tasks, closes clients properly.
- **SQLite WAL mode** enabled for concurrent access (configured in `connection.py`).

## Common Issues

1. **"database is locked"**: SQLite with high concurrency - WAL mode helps but consider PostgreSQL for production
2. **Telethon FloodWait**: Normal, bot handles with exponential backoff
3. **Max API 429**: Rate limiting, handled by MaxClient
4. **Session expired**: Re-run `scripts/auth_telethon.py`
