from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ENV = Path(__file__).resolve().parent.parent.parent.parent / "backend" / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(BACKEND_ENV), extra="ignore")

    database_url: str = (
        "postgresql+asyncpg://ipt:ipt_dev_password@localhost:5432/ipt_marketplace"
    )
    redis_url: str = "redis://localhost:6379/0"
    broker_url: str = "redis://localhost:6379/1"
    result_url: str = "redis://localhost:6379/2"
    gateway_webhook_secret: str = "dev-webhook-secret"
    cache_key_prefix: str = "ipt"
    ws_host: str = "127.0.0.1"
    ws_port: int = 8001


settings = Settings()