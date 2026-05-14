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
