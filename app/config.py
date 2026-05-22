from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    openrouter_api_key: str = Field(default="", alias="OPENROUTER_API_KEY")
    admin_username: str = Field(default="admin", alias="ADMIN_USERNAME")
    admin_password: str = Field(default="change-this-password", alias="ADMIN_PASSWORD")
    database_url: str = Field(default="sqlite+aiosqlite:///./data/app.db", alias="DATABASE_URL")
    openrouter_model: str = Field(default="qwen/qwen3-235b-a22b-2507", alias="OPENROUTER_MODEL")
    app_base_url: str = Field(default="http://localhost:8000", alias="APP_BASE_URL")
    session_secret: str = Field(default="change-this-random-secret", alias="SESSION_SECRET")
    bot_polling: bool = Field(default=True, alias="BOT_POLLING")

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
