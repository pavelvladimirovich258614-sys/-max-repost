# Max-Repost Bot

Telegram bot for reposting content from VK to Telegram channels with payment integration.

## Features

- Repost posts from VK communities to Telegram
- YooKassa payment integration
- Bonus channel subscription verification
- Rate limiting and media processing

## Requirements

- Python 3.11+
- PostgreSQL 15
- Redis 7

## Installation

1. Clone repository
2. Copy `.env.example` to `.env` and fill in your values
3. Install dependencies: `pip install -r requirements.txt`
4. Start services: `docker-compose -f docker/docker-compose.yml up -d`
5. Run migrations: `alembic upgrade head`
6. Start bot: `python -m bot.main`

## Docker

```bash
cd docker
docker-compose up -d
```

## Configuration

See `.env.example` for all available configuration options.

## Project Structure

```
max-repost/
├── bot/                # Main application code
├── config/             # Configuration settings
├── docker/             # Docker configuration
├── alembic/            # Database migrations
└── tests/              # Tests
```
