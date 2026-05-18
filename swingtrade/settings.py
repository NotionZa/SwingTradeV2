from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_config_dir() -> Path:
    env = os.environ.get("SWINGTRADE_CONFIG_DIR")
    if env:
        return Path(env).resolve()
    return Path.cwd().resolve() / "config"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    anthropic_api_key: str = Field(default="", validation_alias="ANTHROPIC_API_KEY")
    newsapi_key: str = Field(
        default="",
        validation_alias=AliasChoices("NEWSAPI_KEY", "NEWS_API_KEY"),
    )
    # Pipe-separated NewsAPI `q` strings for general/macro news. Empty = built-in defaults in macro_news_queries().
    newsapi_macro_queries: str = Field(
        default="",
        validation_alias="NEWSAPI_MACRO_QUERIES",
    )
    finnhub_key: str = Field(default="", validation_alias="FINNHUB_KEY")
    reddit_client_id: str = Field(default="", validation_alias="REDDIT_CLIENT_ID")
    reddit_client_secret: str = Field(default="", validation_alias="REDDIT_CLIENT_SECRET")
    reddit_user_agent: str = Field(
        default="SwingTradeV2/0.1",
        validation_alias="REDDIT_USER_AGENT",
    )

    discord_webhook_daily_briefing: str = Field(
        default="", validation_alias="DISCORD_WEBHOOK_DAILY_BRIEFING"
    )
    discord_webhook_macro_tech: str = Field(
        default="", validation_alias="DISCORD_WEBHOOK_MACRO_TECH"
    )
    discord_webhook_earnings_flow: str = Field(
        default="", validation_alias="DISCORD_WEBHOOK_EARNINGS_FLOW"
    )
    discord_webhook_market_news: str = Field(
        default="", validation_alias="DISCORD_WEBHOOK_MARKET_NEWS"
    )
    discord_webhook_watchlist: str = Field(
        default="", validation_alias="DISCORD_WEBHOOK_WATCHLIST"
    )
    discord_webhook_trade_setups: str = Field(
        default="", validation_alias="DISCORD_WEBHOOK_TRADE_SETUPS"
    )
    discord_webhook_risk_management: str = Field(
        default="", validation_alias="DISCORD_WEBHOOK_RISK_MANAGEMENT"
    )
    discord_webhook_cio_context: str = Field(
        default="", validation_alias="DISCORD_WEBHOOK_CIO_CONTEXT"
    )

    discord_bot_token: str = Field(default="", validation_alias="DISCORD_BOT_TOKEN")
    discord_application_id: str = Field(
        default="", validation_alias="DISCORD_APPLICATION_ID"
    )
    discord_allowed_guild_ids: str = Field(
        default="", validation_alias="DISCORD_ALLOWED_GUILD_IDS"
    )
    discord_allowed_role_ids: str = Field(
        default="", validation_alias="DISCORD_ALLOWED_ROLE_IDS"
    )

    anthropic_model_haiku: str = Field(
        default="claude-haiku-4-5-20251001",
        validation_alias="ANTHROPIC_MODEL_HAIKU",
    )
    anthropic_model_sonnet: str = Field(
        default="claude-sonnet-4-6",
        validation_alias="ANTHROPIC_MODEL_SONNET",
    )
    anthropic_model_opus: str = Field(
        default="claude-opus-4-7",
        validation_alias="ANTHROPIC_MODEL_OPUS",
    )

    swingtrade_config_dir: Path = Field(
        default_factory=_default_config_dir,
        validation_alias=AliasChoices("SWINGTRADE_CONFIG_DIR", "swingtrade_config_dir"),
    )

    http_timeout_seconds: float = Field(default=30.0)
    anthropic_timeout_seconds: float = Field(default=120.0)

    @field_validator("swingtrade_config_dir", mode="before")
    @classmethod
    def _coerce_config_dir(cls, v: object) -> Path:
        if v is None or v == "":
            return _default_config_dir()
        if isinstance(v, Path):
            return v.resolve()
        if isinstance(v, str) and v.strip():
            return Path(v).resolve()
        return _default_config_dir()

    def watchlist_path(self) -> Path:
        return self.swingtrade_config_dir / "watchlist.yaml"

    def universe_path(self) -> Path:
        return self.swingtrade_config_dir / "universe.yaml"

    def macro_news_queries(self) -> list[str]:
        """Broad NewsAPI `q` strings for general market / tech news (not per-ticker)."""
        raw = self.newsapi_macro_queries.strip()
        if raw:
            return [p.strip() for p in raw.split("|") if p.strip()]
        return [
            "Federal Reserve OR interest rates OR inflation OR stock market OR wall street",
            "technology OR semiconductor OR artificial intelligence OR earnings OR guidance",
        ]

    def allowed_guild_ids(self) -> set[int]:
        return _parse_snowflake_set(self.discord_allowed_guild_ids)

    def allowed_role_ids(self) -> set[int]:
        return _parse_snowflake_set(self.discord_allowed_role_ids)


def _parse_snowflake_set(raw: str) -> set[int]:
    out: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.add(int(part))
        except ValueError:
            continue
    return out


@lru_cache
def get_settings() -> Settings:
    return Settings()
