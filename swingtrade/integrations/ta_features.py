from __future__ import annotations

from typing import Any

import pandas as pd


def _rsi_wilder(close: pd.Series, length: int = 14) -> pd.Series:
    """RSI using Wilder-style smoothing (EWMA alpha=1/length)."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1.0 / length, min_periods=length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / length, min_periods=length, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, float("nan"))
    return 100.0 - (100.0 / (1.0 + rs))


def _macd(close: pd.Series) -> tuple[pd.Series, pd.Series]:
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    line = ema12 - ema26
    signal = line.ewm(span=9, adjust=False).mean()
    return line, signal


def _bollinger(close: pd.Series, length: int = 20, num_std: float = 2.0) -> tuple[pd.Series, pd.Series, pd.Series]:
    mid = close.rolling(length, min_periods=length).mean()
    std = close.rolling(length, min_periods=length).std()
    upper = mid + num_std * std
    lower = mid - num_std * std
    return lower, mid, upper


def compute_ta_features(df: pd.DataFrame) -> dict[str, Any]:
    """RSI / MACD / Bollinger / volume ratio using pandas only (no pandas-ta / numba)."""
    if df.empty or "Close" not in df:
        return {"error": "empty_or_missing_close"}
    close = df["Close"].astype(float)
    vol = df.get("Volume", pd.Series(index=df.index, data=0.0)).astype(float)

    last = float(close.iloc[-1])
    row: dict[str, Any] = {"last_close": last}

    rsi = _rsi_wilder(close, 14)
    if rsi.notna().any():
        row["rsi_14"] = float(rsi.iloc[-1])

    macd_line, macd_signal = _macd(close)
    if macd_line.notna().any():
        row["macd"] = float(macd_line.iloc[-1])
        row["macd_signal"] = float(macd_signal.iloc[-1])

    bb_lower, bb_mid, bb_upper = _bollinger(close, 20, 2.0)
    if bb_mid.notna().any():
        row["bb_lower"] = float(bb_lower.iloc[-1])
        row["bb_mid"] = float(bb_mid.iloc[-1])
        row["bb_upper"] = float(bb_upper.iloc[-1])

    vol_ma = vol.rolling(20, min_periods=1).mean()
    if vol_ma.notna().any():
        row["volume_ratio"] = float(vol.iloc[-1] / max(float(vol_ma.iloc[-1]), 1e-9))
    return row
