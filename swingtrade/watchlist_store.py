from __future__ import annotations

import logging
import tempfile
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


def load_watchlist_yaml(path: Path) -> dict[str, list[str]]:
    if not path.exists():
        return {}
    raw = path.read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError("watchlist.yaml root must be a mapping of category -> tickers")
    out: dict[str, list[str]] = {}
    for category, tickers in data.items():
        if not isinstance(category, str):
            raise ValueError("watchlist categories must be strings")
        if tickers is None:
            out[category] = []
            continue
        if not isinstance(tickers, list):
            raise ValueError(f"watchlist[{category!r}] must be a list of tickers")
        cleaned: list[str] = []
        for t in tickers:
            if not isinstance(t, str):
                raise ValueError(f"Invalid ticker under {category!r}")
            u = t.strip().upper()
            if u:
                cleaned.append(u)
        out[category] = cleaned
    return out


def save_watchlist_yaml_atomic(path: Path, data: dict[str, list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    dumped = yaml.safe_dump(
        data,
        default_flow_style=False,
        sort_keys=True,
        allow_unicode=False,
    )
    fd, tmp = tempfile.mkstemp(
        prefix=".watchlist_",
        suffix=".tmp",
        dir=str(path.parent),
    )
    tmp_path = Path(tmp)
    try:
        with open(fd, "w", encoding="utf-8", closefd=True) as f:
            f.write(dumped)
        tmp_path.replace(path)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def watchlist_flat_tickers(data: dict[str, list[str]]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for tickers in data.values():
        for t in tickers:
            if t not in seen:
                seen.add(t)
                ordered.append(t)
    return ordered


def format_watchlist_discord(data: dict[str, list[str]]) -> str:
    lines: list[str] = ["**Watchlist**"]
    for cat in sorted(data.keys()):
        tickers = data[cat]
        lines.append(f"**{cat}:** {', '.join(tickers) if tickers else '_(empty)_'}")
    return "\n".join(lines)
