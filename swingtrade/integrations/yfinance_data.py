from __future__ import annotations

import logging
from typing import Any

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)


def fetch_ohlcv(symbol: str, period: str = "3mo", interval: str = "1d") -> pd.DataFrame:
    t = yf.Ticker(symbol)
    df = t.history(period=period, interval=interval, auto_adjust=True)
    if df is None or df.empty:
        return pd.DataFrame()
    return df


def _pct_change(current: float, base: float) -> float | None:
    if base == 0 or base != base or current != current:
        return None
    return round((current - base) / base * 100.0, 2)


def _safe_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if x != x:
        return None
    return round(x, 4)


def macro_proxy_ohlcv_summary(symbol: str) -> dict[str, Any]:
    """Daily-bar summary for macro proxies (30d window) used by Market Sentiment."""
    df = fetch_ohlcv(symbol, period="30d", interval="1d")
    if df.empty or "Close" not in df:
        return {"symbol": symbol, "error": "no_data"}

    close = df["Close"].astype(float)
    high = df["High"].astype(float) if "High" in df else close
    low = df["Low"].astype(float) if "Low" in df else close

    last_close = _safe_float(close.iloc[-1])
    prev_close = _safe_float(close.iloc[-2]) if len(close) >= 2 else None

    change_pct = None
    if last_close is not None and prev_close is not None:
        change_pct = _pct_change(last_close, prev_close)

    week_change_pct = None
    if len(close) >= 6 and last_close is not None:
        base = _safe_float(close.iloc[-6])
        if base is not None:
            week_change_pct = _pct_change(last_close, base)

    window = close.tail(min(20, len(close)))
    twenty_day_high = _safe_float(window.max())
    twenty_day_low = _safe_float(window.min())

    return {
        "symbol": symbol,
        "last_close": last_close,
        "prev_close": prev_close,
        "change_pct": change_pct,
        "week_change_pct": week_change_pct,
        "day_range_high": _safe_float(high.iloc[-1]),
        "day_range_low": _safe_float(low.iloc[-1]),
        "twenty_day_high": twenty_day_high,
        "twenty_day_low": twenty_day_low,
    }


def last_close_and_adv_usd(symbol: str, lookback: int = 20) -> dict[str, Any]:
    """Approximate ADV in USD using last N daily bars."""
    df = fetch_ohlcv(symbol, period="6mo")
    if df.empty or "Close" not in df or "Volume" not in df:
        return {"symbol": symbol, "error": "no_data"}
    tail = df.tail(lookback)
    last_close = float(tail["Close"].iloc[-1])
    vol = tail["Volume"].astype(float)
    avg_vol = float(vol.mean())
    adv_usd = avg_vol * last_close
    return {
        "symbol": symbol,
        "last_close": last_close,
        "avg_volume": avg_vol,
        "adv_usd": adv_usd,
    }


def bundle_macro_series() -> dict[str, Any]:
    """Compact OHLCV summary for macro proxies used by Market Sentiment agent."""
    symbols = {
        "QQQ": "QQQ",
        "VIX": "^VIX",
        "SOXX": "SOXX",
        "DXY": "DX-Y.NYB",
        "TLT": "TLT",
        "TNX": "^TNX",
    }
    out: dict[str, Any] = {}
    for label, sym in symbols.items():
        out[label] = macro_proxy_ohlcv_summary(sym)
    return out


def ohlcv_for_ticker(symbol: str) -> pd.DataFrame:
    return fetch_ohlcv(symbol, period="1y", interval="1d")


def _coerce_positive_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if x != x or x <= 0:  # NaN or non-positive
        return None
    return x


def _fast_info_pick(fi: Any, *keys: str) -> Any:
    for k in keys:
        v: Any = None
        try:
            if hasattr(fi, "__getitem__"):
                v = fi[k]  # type: ignore[index]
        except Exception:
            v = None
        if v is None and hasattr(fi, "get"):
            try:
                v = fi.get(k)  # type: ignore[union-attr]
            except Exception:
                v = None
        if v is None:
            v = getattr(fi, k, None)
        if v is not None:
            return v
    return None


def fetch_yfinance_market_cap_usd(symbol: str) -> dict[str, Any]:
    """Market cap from Yahoo quote path (updates with the session; not SEC filing shares).

    Uses ``fast_info`` first, then ``info['marketCap']`` as a fallback.
    """
    t = yf.Ticker(symbol)
    market_cap: float | None = None
    last_price: float | None = None

    try:
        fi = t.fast_info
        if fi is not None:
            market_cap = _coerce_positive_float(
                _fast_info_pick(fi, "market_cap", "marketCap")
            )
            last_price = _coerce_positive_float(
                _fast_info_pick(fi, "last_price", "lastPrice")
            )
    except Exception as exc:
        logger.debug("yfinance fast_info for %s: %s", symbol, exc)

    if market_cap is None:
        try:
            info = t.info
            if isinstance(info, dict):
                market_cap = _coerce_positive_float(info.get("marketCap"))
        except Exception as exc:
            logger.debug("yfinance info marketCap for %s: %s", symbol, exc)

    return {"market_cap_usd": market_cap, "last_price": last_price}
