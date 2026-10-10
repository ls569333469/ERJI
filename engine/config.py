from functools import lru_cache
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ERJI_", env_file=".env", extra="ignore")
    database_url: str = "postgresql+psycopg://erji:local-development-only@127.0.0.1:55432/erji"
    mode: str = "research_live"
    web_origin: str = "http://127.0.0.1:18770"
    api_origin: str = "http://127.0.0.1:18771"
    collector_interval: int = 60
    symbols: str = "BTC,ETH,SOL,HYPE"
    cmc_key_file: str | None = None
    bykaranteli_key_file: str | None = None
    telegram_token_file: str | None = None
    admin_token_file: str | None = None
    require_auth: bool = False
    session_seconds: int = 28800
    cookie_path: str = "/"

    @field_validator("cookie_path")
    @classmethod
    def valid_cookie_path(cls, value):
        if value != "/" and (not value.startswith("/") or value.endswith("/") or any(c in value for c in ";\r\n?#")):
            raise ValueError("Invalid cookie path")
        return value

@lru_cache
def settings() -> Settings:
    return Settings()

