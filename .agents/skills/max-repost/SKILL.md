---
name: max-repost
description: Telegram bot transferring posts from TG channels to Max messenger
---

## Project: max-repost

Stack: Python 3.12, aiogram 3, Telethon, SQLAlchemy async, SQLite, Max Bot API

Architecture:
- bot/telegram/handlers/ — aiogram callback/message handlers
- bot/core/ — business logic (transfer_engine, autopost)
- bot/max_api/ — Max messenger API client
- bot/database/ — SQLAlchemy models + repository pattern

Rules:
- All callbacks must call callback.answer() FIRST
- Use edit_text instead of answer (no message stacking)
- ADMIN_IDS from .env — unlimited transfers, no balance check
- Autopost = monitoring NEW posts via Telethon events.NewMessage
- Balance in rubles (UserBalance), not post count
- Repositories injected via middleware (db.py)