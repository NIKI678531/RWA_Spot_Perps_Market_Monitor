"""Application settings, read from ``backend/.env``."""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

#: How long a SQLite connection waits for a lock before raising. Sized against a
#: collection pass, not a query: the hourly pass paces roughly 85 requests across
#: CoinGecko and GeckoTerminal and holds its transaction for over ten minutes.
#: Deployments use MySQL, where this does not apply; local development is the case
#: that has to survive a scheduler and a dashboard on one file.
SQLITE_BUSY_TIMEOUT_SECONDS = 900.0


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
    #: Free accounts get IEX, a single venue carrying a few per cent of US equity
    #: volume. Stored on every row rather than assumed, so a later upgrade to the
    #: consolidated tape ("sip") is visible in the data instead of silently changing
    #: what the series means halfway along.
    alpaca_feed: str = "iex"
    loris_api_key: str = ""
    loris_base_url: str = "https://loris.tools"
    #: The host the loris.tools front end actually calls. The public web pages render
    #: their numbers client-side, so scraping the HTML yields venue names without any
    #: volume or open interest; this API returns both and is the only usable route.
    loris_api_base_url: str = "https://api.loris.tools"

    # --- Cross-venue perpetual exchanges ----------------------------------
    # Public market-data endpoints, no key. These reconstruct the cross-venue perp
    # aggregation that the workbook took from the Loris public page, which is capped
    # at a Top 25 with no history and whose API is key-gated.
    okx_base_url: str = "https://www.okx.com"
    bybit_base_url: str = "https://api.bybit.com"
    gate_base_url: str = "https://api.gateio.ws"
    mexc_futures_base_url: str = "https://contract.mexc.com"
    bitget_base_url: str = "https://api.bitget.com"

    # --- Issuer official sites --------------------------------------------
    # Product breadth comes from the issuers themselves: their own counts exceed any
    # aggregator's index, so these pages are the coverage denominator.
    ondo_products_url: str = "https://ondo.finance/ondo-stocks"
    xstocks_products_url: str = "https://xstocks.com/products"
    xstocks_ecosystem_url: str = "https://xstocks.com/ecosystem"

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
        if not self.is_sqlite:
            return {}
        return {
            "check_same_thread": False,
            # A collection pass holds its write transaction across every HTTP call it
            # makes, which for a rate-limited source is minutes. The default timeout
            # is five seconds, so anything else touching the database in that window
            # fails outright rather than waiting. Waiting is the right answer: a pass
            # that starts a little late is a pass, and one that raises is a hole.
            "timeout": SQLITE_BUSY_TIMEOUT_SECONDS,
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
