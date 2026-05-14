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
    }
    out: dict[str, Any] = {}
    for label, sym in symbols.items():
        info = last_close_and_adv_usd(sym, lookback=5)
        out[label] = info
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
