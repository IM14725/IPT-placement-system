from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ENV = Path(__file__).resolve().parent.parent.parent.parent / "backend" / ".env"

_INSECURE_WEBHOOK_SECRETS = {"", "dev-webhook-secret", "dev-mock-webhook-secret", "MOCK_API_SECRET"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(BACKEND_ENV), extra="ignore")

    django_env: str = "dev"
    database_url: str = (
        "postgresql+asyncpg://ipt:ipt_dev_password@127.0.0.1:5432/ipt_marketplace"
    )
    redis_url: str = "redis://127.0.0.1:6379/0"
    broker_url: str = "redis://127.0.0.1:6379/1"
    result_url: str = "redis://127.0.0.1:6379/2"
    gateway_webhook_secret: str = "dev-webhook-secret"
    mock_gateway_webhook_secret: str = "dev-mock-webhook-secret"
    enable_mock_gateway: bool = False
    cors_allowed_origins: list[str] = ["http://localhost:8000", "http://127.0.0.1:8000"]
    cache_key_prefix: str = "ipt"
    trusted_proxy_count: int = 0
    ws_host: str = "127.0.0.1"
    ws_port: int = 8001

    @model_validator(mode="after")
    def validate_production_settings(self):
        if self.django_env == "prod":
            if self.gateway_webhook_secret in _INSECURE_WEBHOOK_SECRETS or len(self.gateway_webhook_secret) < 16:
                raise ValueError(
                    "Production requires a strong GATEWAY_WEBHOOK_SECRET (cannot use dev default)."
                )
            if "ipt_dev_password" in self.database_url:
                raise ValueError(
                    "Production DATABASE_URL cannot use the default development password."
                )
            if self.enable_mock_gateway:
                raise ValueError(
                    "ENABLE_MOCK_GATEWAY cannot be enabled when DJANGO_ENV=prod."
                )
        return self


settings = Settings()

