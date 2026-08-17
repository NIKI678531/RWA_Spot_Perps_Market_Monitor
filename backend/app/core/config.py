"""Application settings, read from ``backend/.env``."""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Every field has a local-development default."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    environment: str = "local"
    database_url: str = "sqlite:///./app.db"
    api_base_path: str = "/"
    frontend_origins: str | list[str] = "*"

    # --- Data sources -----------------------------------------------------
    coingecko_api_key: str = ""
    coingecko_base_url: str = "https://api.coingecko.com/api/v3"
    geckoterminal_base_url: str = "https://api.geckoterminal.com/api/v2"
    hyperliquid_base_url: str = "https://api.hyperliquid.xyz"
    binance_base_url: str = "https://api.binance.com"
    binance_fapi_base_url: str = "https://fapi.binance.com"
    alpaca_api_key_id: str = ""
    alpaca_api_secret_key: str = ""
    alpaca_base_url: str = "https://data.alpaca.markets"
    loris_api_key: str = ""
    loris_base_url: str = "https://loris.tools"

    # --- Report delivery --------------------------------------------------
    # No PVC in production K8s: generated xlsx/docx go to object storage or the DB.
    report_storage_backend: str = "database"
    tos_endpoint: str = ""
    tos_bucket: str = ""
    tos_access_key: str = ""
    tos_secret_key: str = ""

    # --- Scheduling -------------------------------------------------------
    scheduler_enabled: bool = False
    scheduler_timezone: str = "Asia/Hong_Kong"
    daily_report_cron: str = "0 8 * * *"

    @field_validator("frontend_origins")
    @classmethod
    def _parse_origins(cls, value: str | list[str]) -> str | list[str]:
        """Accept ``*``, a JSON list, or a comma-separated string."""
        if isinstance(value, list):
            return value
        text = value.strip()
        if text.startswith("["):
            parsed: Any = json.loads(text)
            return [str(item) for item in parsed]
        return text

    @property
    def cors_allow_origins(self) -> list[str]:
        if isinstance(self.frontend_origins, list):
            return self.frontend_origins
        if self.frontend_origins.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.frontend_origins.split(",") if o.strip()]

    @property
    def cors_allow_credentials(self) -> bool:
        """Credentials are refused while the origin list is a wildcard.

        ``*`` plus ``allow_credentials`` is the combination browsers reject outright
        and servers should never offer: it invites any site to make authenticated
        cross-origin calls. The default deployment is same-origin behind nginx and
        needs no credentialed CORS at all, so the wildcard stays convenient for local
        development without also being the setting that opens the API up.
        """
        return "*" not in self.cors_allow_origins

    @property
    def normalized_api_base_path(self) -> str:
        """Router prefix. ``/`` means "no prefix", not a literal slash prefix."""
        base = self.api_base_path.strip()
        if base in {"", "/"}:
            return ""
        return "/" + base.strip("/")

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def sqlite_connect_args(self) -> dict[str, Any]:
        return {"check_same_thread": False} if self.is_sqlite else {}


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
