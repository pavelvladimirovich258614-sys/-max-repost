"""Application settings using Pydantic Settings."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Telegram Bot
    telegram_bot_token: str = Field(..., alias="TELEGRAM_BOT_TOKEN")
    admin_telegram_id: int = Field(..., alias="ADMIN_TELEGRAM_ID")
    admin_ids: str = Field(default="", alias="ADMIN_IDS")

    @property
    def ADMIN_IDS(self) -> list[int]:
        """Return list of admin Telegram IDs for easy access."""
        if not self.admin_ids:
            return []
        return [int(x.strip()) for x in self.admin_ids.split(",") if x.strip()]

    # Telegram API (for Telethon - user session)
    telegram_api_id: int = Field(..., alias="TELEGRAM_API_ID")
    telegram_api_hash: str = Field(..., alias="TELEGRAM_API_HASH")
    telegram_phone: str = Field(..., alias="TELEGRAM_PHONE")

    # Max API (vk.com)
    max_access_token: str = Field(..., alias="MAX_ACCESS_TOKEN")

    # Database
    database_url: str = Field(
        default="sqlite+aiosqlite:///./data/bot.db",
        alias="DATABASE_URL",
    )

    # Redis
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        alias="REDIS_URL",
    )

    # YooKassa Payment
    yookassa_shop_id: str = Field(..., alias="YOOKASSA_SHOP_ID")
    yookassa_secret_key: str = Field(..., alias="YOOKASSA_SECRET_KEY")
    yookassa_return_url: str = Field(default="https://t.me/maxx_repost_bot", alias="YOOKASSA_RETURN_URL")
    receipt_email: str = Field(default="support@maxrepost.ru", alias="RECEIPT_EMAIL")

    # Logging
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    debug: bool = Field(default=False, alias="DEBUG")

    # Rate Limiting
    max_rps: int = Field(default=25, alias="MAX_RPS")

    # Bonus Channel
    bonus_channel: str = Field(..., alias="BONUS_CHANNEL")

    # Pricing
    price_per_post: int = Field(default=3, alias="PRICE_PER_POST")
    free_posts_bonus: int = Field(default=10, alias="FREE_POSTS_BONUS")


# Global settings instance
settings = Settings()
