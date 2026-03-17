# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**max-repost** is a Telegram bot that transfers posts from Telegram channels to Max messenger (formerly VK Teams/ICQ). Users select a TG channel, a Max channel, and the number of posts to transfer. The bot downloads media from TG via Telethon (user session), uploads to Max API, and posts 1:1.

Business model: Paid transfers at 3 RUB/post, with 5 free trial posts per user.

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
# Run Alembic migrations (if using PostgreSQL)
alembic upgrade head

# Create a new migration
alembic revision --autogenerate -m "description"
```

### Docker Services (for PostgreSQL/Redis)
```bash
cd docker
docker-compose up -d
```

### Telethon Authentication
The bot requires a Telethon **user session** (not bot token) to read TG channel history. The session file `user_session.session` must exist in the project root.

To authorize:
```bash
python scripts/auth_telethon.py
```

## Architecture

### Core Components

**`bot/core/transfer_engine.py`** - Main transfer orchestration
- Fetches posts from TG via Telethon (oldest first)
- Downloads media to BytesIO, uploads to Max API
- Handles text formatting (Telegram entities → HTML for Max)
- Progress callbacks for UI updates
- Duplicate protection via `TransferredPostRepository`
- Aborts after 5 consecutive errors

**`bot/max_api/client.py`** - Max Platform API client
- Rate-limited requests (token bucket, default 25 RPS)
- Two-step file upload: `POST /uploads` → upload to URL → get token
- Retry logic for 429 and 5xx errors
- Token location varies by media type:
  - **image/file**: token in step 2 response, nested under `{"photos": {"<hash>": {"token": "..."}}}`
  - **video/audio**: token in step 1 response

**`bot/core/telethon_client.py`** - TG channel history reader
- Uses MTProto API (requires user session, not bot)
- `count_channel_posts()` - efficient count without fetching messages
- `iter_messages()` - memory-efficient iteration with `reverse=True`

### Telegram Bot (aiogram)

**FSM Storage**: `MemoryStorage` (in-memory, not Redis)

**State Groups** (`bot/telegram/states.py`):
- `AutopostStates` - Auto-repost setup flow
- `TransferStates` - Manual transfer flow (TG channel → verify → Max channel → count)
- `ChannelStates` - Channel management

**Handler Registration Order** (matters - more specific first):
1. start_router
2. autopost_router
3. transfer_router
4. channels_router

### Database

**ORM**: SQLAlchemy 2.0 with async

**Connection**: SQLite (`./data/bot.db`) via aiosqlite. PostgreSQL was planned but SQLite is currently used.

**Key Models** (`bot/database/models.py`):
- `User` - balance, `free_posts_used` (max 5), admin flag
- `Channel` - TG→Max bindings, auto_repost flag
- `Post` - tracking reposted content
- `TransferredPost` - duplicate protection (unique per TG channel + Max channel + msg_id)
- `MaxChannelBinding` - saved Max channels for quick reuse
- `VerifiedChannel` - verified TG channel ownership
- `Payment` - YooKassa transactions

**Repositories**: All in `bot/database/repositories/`. Use repository pattern, not direct ORM access in handlers.

### Text Formatting

**`bot/core/transfer_engine.py`**:
- `convert_entities_to_html()` - Converts Telegram formatting entities (bold, italic, links, etc.) to HTML for Max API
- Handles UTF-16 offsets for emoji compatibility
- `split_text()` - Splits text >4000 chars into chunks

## Configuration

**File**: `config/settings.py` (Pydantic Settings)

**Key .env variables**:
- `TELEGRAM_API_ID`/`HASH` - For Telethon (get from https://my.telegram.org)
- `TELEGRAM_PHONE` - User session phone number
- `MAX_ACCESS_TOKEN` - Max Platform API token
- `DATABASE_URL` - Defaults to SQLite
- `PRICE_PER_POST` - Default 3 (RUB)
- `MAX_RPS` - Rate limit for Max API (default 25)

## Common Patterns

### Transfer Workflow
```python
from bot.core.transfer_engine import TransferEngine
from bot.core.telethon_client import get_telethon_client
from bot.max_api.client import MaxClient

telethon = get_telethon_client(...)
async with MaxClient() as max_client:
    engine = TransferEngine(telethon, max_client, db_session, user_id, tg_channel, max_channel_id)
    result = await engine.transfer_posts(
        tg_channel="@channel",
        max_channel_id=12345,
        count=10,
        progress_callback=callback,
    )
    # result: success, failed, skipped, duplicates counts
```

### Database Session in Handlers
Use middleware injection:
```python
@dp.message(ChannelStates.viewing_channel)
async def handler(message: Message, state: FSMContext, session: AsyncSession):
    repo = UserRepository(session)
    user = await repo.get_by_telegram_id(message.from_user.id)
```

### Max File Upload
```python
# For images
token = await max_client.upload_image(file_bytes)
attachment = {"type": "image", "payload": {"token": token}}
await max_client.send_message(chat_id, text, attachments=[attachment], format="html")
```

## Important Notes

- **Telethon session must exist** before running bot. Run auth script if missing.
- **Max API tokens vary by media type** - check response structure carefully.
- **Duplicate protection** is enforced at database level - don't bypass.
- **Rate limiting** is built into MaxClient - don't add extra delays around it.
- **FSM uses MemoryStorage** - state is lost on bot restart.
- **Text chunks** >4000 chars are split into multiple Max messages.
- **Album posts** with grouped_id are handled but only first media is transferred (TODO: full album support).
