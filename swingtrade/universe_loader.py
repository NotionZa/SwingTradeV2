from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from swingtrade.watchlist_store import watchlist_flat_tickers

logger = logging.getLogger(__name__)


def load_universe_yaml(path: Path) -> list[str]:
    if not path.exists():
        logger.warning("Universe file missing at %s — using empty universe", path)
        return []
    raw = path.read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    if data is None:
        return []
    tickers: list[Any]
    if isinstance(data, dict) and "tickers" in data:
        tickers = data["tickers"]  # type: ignore[assignment]
    elif isinstance(data, list):
        tickers = data
    else:
        raise ValueError("universe.yaml must be a list of tickers or {tickers: [...]}")
    out: list[str] = []
    seen: set[str] = set()
    for t in tickers:
        if not isinstance(t, str):
            continue
        u = t.strip().upper()
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out


def merge_watchlist_into_universe(
    universe: list[str], watchlist: dict[str, list[str]]
) -> list[str]:
    """Pipeline tickers: union of universe and all watchlist categories, stable order."""
    wl = watchlist_flat_tickers(watchlist)
    seen = set(universe)
    merged = list(universe)
    for t in wl:
        if t not in seen:
            seen.add(t)
            merged.append(t)
    return merged
