from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import httpx

from swingtrade.settings import Settings

logger = logging.getLogger(__name__)


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
