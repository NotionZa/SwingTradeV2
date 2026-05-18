from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx

from swingtrade.settings import Settings

logger = logging.getLogger(__name__)

_FINNHUB_NEWS_URL = "https://finnhub.io/api/v1/news"
_FINNHUB_COMPANY_NEWS_URL = "https://finnhub.io/api/v1/company-news"

_GENERAL_MACRO_KEYWORDS: tuple[str, ...] = (
    "fed",
    "federal reserve",
    "rates",
    "inflation",
    "treasury",
    "gdp",
    "cpi",
    "nfp",
    "tariff",
    "geopolitical",
)


def _finnhub_key(settings: Settings) -> str:
    return settings.finnhub_key.strip()


def _format_finnhub_news_item(item: dict[str, Any], *, category: str) -> dict[str, Any]:
    ts = item.get("datetime")
    if isinstance(ts, (int, float)) and ts > 0:
        dt_str = datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
    else:
        dt_str = ""
    return {
        "headline": str(item.get("headline") or item.get("title") or "").strip(),
        "source": str(item.get("source") or "").strip(),
        "datetime": dt_str,
        "category": category,
    }


def _dedupe_headlines(
    items: list[dict[str, Any]], limit: int
) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, Any]] = []
    for item in items:
        key = (item.get("headline") or "", item.get("source") or "")
        if not key[0] or key in seen:
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= limit:
            break
    return out


def _general_article_matches_macro_filter(item: dict[str, Any]) -> bool:
    text = f"{item.get('headline') or ''} {item.get('summary') or ''}".lower()
    return any(kw in text for kw in _GENERAL_MACRO_KEYWORDS)


def _fetch_finnhub_news_category(
    client: httpx.Client,
    *,
    api_key: str,
    category: str,
) -> list[dict[str, Any]]:
    r = client.get(
        _FINNHUB_NEWS_URL,
        params={"category": category, "token": api_key},
    )
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, list):
        logger.warning("Finnhub news unexpected payload for category=%s", category)
        return []
    return [x for x in data if isinstance(x, dict)]


def finnhub_market_news(
    settings: Settings,
    client: httpx.Client,
    *,
    tech_limit: int = 15,
    general_limit: int = 10,
    max_total: int = 20,
) -> list[dict[str, Any]]:
    """Technology + filtered general macro headlines from Finnhub market news."""
    key = _finnhub_key(settings)
    if not key:
        return []

    merged: list[dict[str, Any]] = []
    try:
        tech_raw = _fetch_finnhub_news_category(client, api_key=key, category="technology")
        for item in tech_raw[:tech_limit]:
            row = _format_finnhub_news_item(item, category="technology")
            if row["headline"]:
                merged.append(row)

        general_raw = _fetch_finnhub_news_category(client, api_key=key, category="general")
        general_kept = 0
        for item in general_raw:
            if general_kept >= general_limit:
                break
            if not _general_article_matches_macro_filter(item):
                continue
            row = _format_finnhub_news_item(item, category="general")
            if row["headline"]:
                merged.append(row)
                general_kept += 1
    except httpx.HTTPError as e:
        logger.warning("Finnhub market news failed: %s", type(e).__name__)
        return _dedupe_headlines(merged, max_total)

    return _dedupe_headlines(merged, max_total)


def finnhub_company_news(
    settings: Settings,
    client: httpx.Client,
    symbol: str,
    from_date: date,
    to_date: date,
    *,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Company-specific headlines from Finnhub for the given date range."""
    key = _finnhub_key(settings)
    if not key:
        return []
    sym = symbol.strip().upper()
    try:
        r = client.get(
            _FINNHUB_COMPANY_NEWS_URL,
            params={
                "symbol": sym,
                "from": from_date.isoformat(),
                "to": to_date.isoformat(),
                "token": key,
            },
        )
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, list):
            logger.warning("Finnhub company-news unexpected payload for %s", sym)
            return []
        formatted: list[dict[str, Any]] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            row = _format_finnhub_news_item(item, category="company")
            if row["headline"]:
                formatted.append(row)
        return _dedupe_headlines(formatted, limit)
    except httpx.HTTPError as e:
        logger.warning("Finnhub company news failed for %s: %s", sym, type(e).__name__)
        return []


def finnhub_earnings_within_days(
    settings: Settings,
    symbol: str,
    days: int = 7,
    client: httpx.Client | None = None,
) -> tuple[bool, str]:
    """Return (has_earnings_within_window, detail)."""
    key = settings.finnhub_key.strip()
    if not key:
        return False, "finnhub_disabled"
    own_client = client is None
    if own_client:
        client = httpx.Client(timeout=settings.http_timeout_seconds)
    try:
        now = datetime.now(timezone.utc).date()
        end = now + timedelta(days=days)
        url = "https://finnhub.io/api/v1/calendar/earnings"
        r = client.get(  # type: ignore[union-attr]
            url,
            params={
                "from": now.isoformat(),
                "to": end.isoformat(),
                "symbol": symbol,
                "token": key,
            },
        )
        r.raise_for_status()
        data = r.json()
        earnings_calendar = data.get("earningsCalendar") or []
        for row in earnings_calendar:
            if row.get("symbol") and row.get("symbol").upper() != symbol.upper():
                continue
            d = row.get("date")
            if not d:
                continue
            try:
                ed = datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc).date()
            except ValueError:
                continue
            if now <= ed <= end:
                return True, f"earnings_on={d}"
        return False, "no_earnings_in_window"
    except httpx.HTTPError as e:
        logger.warning("Finnhub error for %s: %s", symbol, type(e).__name__)
        return False, f"finnhub_error:{type(e).__name__}"
    finally:
        if own_client and client is not None:
            client.close()
