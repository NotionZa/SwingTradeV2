from __future__ import annotations

import csv
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

from swingtrade.candidate_review import (
    _load_jsonl,
    enrich_records_for_review_export,
    select_records_for_review_export,
)
from swingtrade.integrations.ta_features import compute_ta_features
from swingtrade.integrations.yfinance_data import fetch_ohlcv, ohlcv_for_ticker
from swingtrade.settings import Settings
from swingtrade.trade_math import (
    OPPORTUNITY_BUY_NOW,
    OPPORTUNITY_NEAR_BUY,
    OPPORTUNITY_PULLBACK_REQUIRED,
    enrich_candidate_trade_fields,
)
from swingtrade.universe_loader import (
    context_only_tickers,
    load_universe_yaml,
    merge_watchlist_into_universe,
)
from swingtrade.watchlist_store import load_watchlist_yaml

logger = logging.getLogger(__name__)

SOURCE_CORE = "core"
SOURCE_ACTIVE = "active"
SOURCE_DISCOVERY = "discovery"

DEFAULT_ACTIVE_PULLBACK_RR_GAP_MAX = 1.0
DEFAULT_MAX_DISCOVERY = 15
DEFAULT_ANALYSIS_CAP = 30
DEFAULT_CIO_CAP = 12

ACTIVE_OPPORTUNITY_PRE_RANK_BONUS = 2.0

_OPPORTUNITY_STATUS_PRE_RANK = {
    OPPORTUNITY_BUY_NOW: 3.0,
    OPPORTUNITY_NEAR_BUY: 2.0,
    OPPORTUNITY_PULLBACK_REQUIRED: 1.0,
}


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return float(int(value))
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value.strip():
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _normalize_ticker(value: Any) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip().upper()
    return ""


def _normalize_opportunity_status(record: dict[str, Any]) -> str:
    status = record.get("opportunity_status")
    if isinstance(status, str) and status.strip():
        return status.strip().upper()
    return ""


def rough_feature_score(feats: dict[str, Any]) -> float | None:
    """Heuristic 0–10 screen from local indicators (aligned with TA fallback)."""
    if not isinstance(feats, dict) or feats.get("error"):
        return None
    if feats.get("last_close") is None:
        return None
    score = 5.0
    rsi = _as_float(feats.get("rsi_14"))
    if rsi is not None:
        if 50.0 <= rsi <= 70.0:
            score += 1.0
        elif rsi > 70.0:
            score += 0.5
        elif rsi < 30.0:
            score += 0.5
    macd = _as_float(feats.get("macd"))
    macd_sig = _as_float(feats.get("macd_signal"))
    if macd is not None and macd_sig is not None and macd > macd_sig:
        score += 0.5
    last = _as_float(feats.get("last_close"))
    bb_mid = _as_float(feats.get("bb_mid"))
    if last is not None and bb_mid is not None and last > bb_mid:
        score += 0.5
    vol_ratio = _as_float(feats.get("volume_ratio"))
    if vol_ratio is not None and vol_ratio >= 1.0:
        score += 0.3
    return min(score, 10.0)


def load_universe_pools_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    raw = path.read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    return data if isinstance(data, dict) else {}


def load_discovery_candidates(config: dict[str, Any]) -> list[str]:
    raw = config.get("discovery_candidates")
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        sym = _normalize_ticker(item)
        if sym and sym not in seen:
            seen.add(sym)
            out.append(sym)
    limits = config.get("limits")
    max_disc = DEFAULT_MAX_DISCOVERY
    if isinstance(limits, dict):
        cap = limits.get("max_discovery")
        if isinstance(cap, int) and cap > 0:
            max_disc = cap
    return out[:max_disc]


def active_pullback_rr_gap_max(config: dict[str, Any]) -> float:
    limits = config.get("limits")
    if isinstance(limits, dict):
        v = _as_float(limits.get("active_pullback_rr_gap_max"))
        if v is not None and v >= 0:
            return v
    return DEFAULT_ACTIVE_PULLBACK_RR_GAP_MAX


def resolve_default_max_trade_pool(settings: Settings) -> int:
    """Default trade-pool cap: full operator core watchlist (no silent tail truncation)."""
    wl = load_watchlist_yaml(settings.watchlist_path())
    uni = load_universe_yaml(settings.universe_path())
    core = load_core_watchlist(uni, wl)
    return len(core) if core else 1


def resolve_max_trade_pool(settings: Settings, max_tickers: int | None) -> int:
    """Explicit --max-tickers wins; otherwise cover the full core watchlist."""
    if max_tickers is not None and max_tickers > 0:
        return max_tickers
    return resolve_default_max_trade_pool(settings)


def load_core_watchlist(
    universe: list[str],
    watchlist: dict[str, list[str]],
) -> list[str]:
    """Operator-curated trade names: universe ∪ watchlist minus context-only proxies."""
    merged = merge_watchlist_into_universe(universe, watchlist)
    ctx_only = context_only_tickers(watchlist)
    return [t for t in merged if t not in ctx_only]


def is_active_opportunity_record(
    record: dict[str, Any],
    *,
    pullback_rr_gap_max: float = DEFAULT_ACTIVE_PULLBACK_RR_GAP_MAX,
) -> bool:
    status = _normalize_opportunity_status(record)
    if status in (OPPORTUNITY_BUY_NOW, OPPORTUNITY_NEAR_BUY):
        return True
    if status == OPPORTUNITY_PULLBACK_REQUIRED:
        gap = _as_float(record.get("rr_gap"))
        return gap is not None and gap <= pullback_rr_gap_max
    return False


def qualify_active_opportunity_records(
    records: list[dict[str, Any]],
    *,
    pullback_rr_gap_max: float = DEFAULT_ACTIVE_PULLBACK_RR_GAP_MAX,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in records:
        sym = _normalize_ticker(record.get("ticker"))
        if not sym or sym in seen:
            continue
        if is_active_opportunity_record(
            record, pullback_rr_gap_max=pullback_rr_gap_max
        ):
            seen.add(sym)
            out.append(record)
    return out


def default_data_dir() -> Path:
    return Path.cwd().resolve() / "data"


def _latest_data_file(
    directory: Path,
    pattern: str,
) -> Path | None:
    if not directory.is_dir():
        return None
    files = [p for p in directory.glob(pattern) if p.is_file()]
    if not files:
        return None
    return max(files, key=lambda p: p.stat().st_mtime)


def find_latest_opportunity_source(
    data_dir: Path | None = None,
) -> tuple[Literal["csv", "jsonl"], Path] | None:
    """Prefer newest opportunity CSV; fall back to candidate JSONL."""
    base = data_dir or default_data_dir()
    opp = _latest_data_file(base / "opportunities", "*_opportunities.csv")
    cand = _latest_data_file(base / "candidates", "*.jsonl")
    if opp is None and cand is None:
        return None
    if opp is None:
        assert cand is not None
        return ("jsonl", cand)
    if cand is None:
        return ("csv", opp)
    if opp.stat().st_mtime >= cand.stat().st_mtime:
        return ("csv", opp)
    return ("jsonl", cand)


def _load_opportunity_csv(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if isinstance(row, dict):
                records.append(dict(row))
    return records


def load_active_opportunity_records(
    *,
    data_dir: Path | None = None,
    pullback_rr_gap_max: float = DEFAULT_ACTIVE_PULLBACK_RR_GAP_MAX,
    source_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Load qualifying active-opportunity rows from the latest export or JSONL."""
    if source_path is not None:
        path = source_path.resolve()
        if path.suffix.lower() == ".jsonl":
            loaded = _load_jsonl(path)
            records = select_records_for_review_export(loaded, all_runs=False)
            records = enrich_records_for_review_export(records)
        else:
            records = [_csv_row_to_record(row) for row in _load_opportunity_csv(path)]
            records = [
                enrich_candidate_trade_fields(dict(r))
                for r in records
                if _normalize_ticker(r.get("ticker"))
            ]
    else:
        found = find_latest_opportunity_source(data_dir)
        if found is None:
            return []
        kind, path = found
        if kind == "jsonl":
            loaded = _load_jsonl(path)
            records = select_records_for_review_export(loaded, all_runs=False)
            records = enrich_records_for_review_export(records)
        else:
            records = [_csv_row_to_record(row) for row in _load_opportunity_csv(path)]
            records = [
                enrich_candidate_trade_fields(dict(r))
                for r in records
                if _normalize_ticker(r.get("ticker"))
            ]

    return qualify_active_opportunity_records(
        records, pullback_rr_gap_max=pullback_rr_gap_max
    )


def _csv_row_to_record(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in row.items():
        if value is None or value == "":
            continue
        if key in (
            "rank_score",
            "analysis_rank",
            "cio_score",
            "ta_score",
            "sentiment_score",
            "risk_reward",
            "model_risk_reward",
            "required_rr",
            "valid_entry_max",
            "current_rr",
            "rr_gap",
            "entry_improvement_needed",
        ):
            parsed = _as_float(value)
            if parsed is not None:
                out[key] = parsed
                continue
        if key == "math_valid":
            out[key] = str(value).strip().lower() in ("true", "1", "yes")
            continue
        out[key] = value
    return out


@dataclass
class TradePoolEntry:
    ticker: str
    sources: list[str] = field(default_factory=list)


@dataclass
class TradePoolResult:
    included: list[TradePoolEntry]
    truncated: list[TradePoolEntry]
    core_count: int
    active_count: int
    discovery_count: int
    active_records: list[dict[str, Any]] = field(default_factory=list)

    @property
    def active_tickers(self) -> set[str]:
        return {_normalize_ticker(r.get("ticker")) for r in self.active_records} - {""}

    @property
    def prior_by_ticker(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for record in self.active_records:
            sym = _normalize_ticker(record.get("ticker"))
            if sym:
                out[sym] = record
        return out


def build_trade_pool(
    *,
    universe: list[str],
    watchlist: dict[str, list[str]],
    pools_config: dict[str, Any] | None = None,
    active_records: list[dict[str, Any]] | None = None,
    max_trade_pool: int | None = None,
    data_dir: Path | None = None,
) -> TradePoolResult:
    """Merge core + active + discovery pools; dedupe; preserve source labels."""
    config = pools_config or {}
    gap_max = active_pullback_rr_gap_max(config)
    core = load_core_watchlist(universe, watchlist)
    active_recs = (
        active_records
        if active_records is not None
        else load_active_opportunity_records(
            data_dir=data_dir,
            pullback_rr_gap_max=gap_max,
        )
    )
    discovery = load_discovery_candidates(config)

    source_map: dict[str, list[str]] = {}
    order: list[str] = []

    def _add(sym: str, source: str) -> None:
        if not sym:
            return
        if sym not in source_map:
            order.append(sym)
            source_map[sym] = []
        if source not in source_map[sym]:
            source_map[sym].append(source)

    for record in active_recs:
        _add(_normalize_ticker(record.get("ticker")), SOURCE_ACTIVE)
    for sym in core:
        _add(sym, SOURCE_CORE)
    for sym in discovery:
        _add(sym, SOURCE_DISCOVERY)

    entries = [
        TradePoolEntry(ticker=sym, sources=list(source_map[sym])) for sym in order
    ]
    cap = (
        max_trade_pool
        if max_trade_pool is not None and max_trade_pool > 0
        else len(entries)
    )
    included = entries[:cap]
    truncated = entries[cap:]

    active_only = sum(1 for e in entries if SOURCE_ACTIVE in e.sources)
    discovery_only = sum(
        1 for e in entries if SOURCE_DISCOVERY in e.sources and SOURCE_CORE not in e.sources
    )

    logger.info(
        "Trade pool: core=%s active_qualifying=%s discovery=%s merged=%s "
        "included=%s truncated=%s (max_trade_pool=%s)",
        len(core),
        active_only,
        len(discovery),
        len(entries),
        len(included),
        len(truncated),
        cap,
    )

    return TradePoolResult(
        included=included,
        truncated=truncated,
        core_count=len(core),
        active_count=active_only,
        discovery_count=discovery_only,
        active_records=active_recs,
    )


def _empty_survivor_features() -> dict[str, Any]:
    return {
        "features": {},
        "vs_qqq_close_ratio": None,
        "rough_feature_score": None,
        "volume_ratio": None,
    }


def compute_survivor_features(
    symbols: list[str],
    *,
    fetch_features: bool = True,
) -> dict[str, dict[str, Any]]:
    """Deterministic per-symbol features for pre-rank (yfinance; no LLM)."""
    if not fetch_features or not symbols:
        return {}

    qqq_last: float | None = None
    try:
        qqq = fetch_ohlcv("QQQ", period="6mo")
        qqq_last = float(qqq["Close"].iloc[-1]) if not qqq.empty else None
    except Exception as exc:
        logger.warning("Pre-rank QQQ feature fetch failed (non-fatal): %s", exc)

    out: dict[str, dict[str, Any]] = {}
    for sym in symbols:
        key = _normalize_ticker(sym)
        if not key:
            continue
        try:
            df = ohlcv_for_ticker(key)
            feats = compute_ta_features(df)
            rel = None
            last = _as_float(feats.get("last_close"))
            if qqq_last and last:
                rel = last / qqq_last
            rough = rough_feature_score(feats) if isinstance(feats, dict) else None
            out[key] = {
                "features": feats,
                "vs_qqq_close_ratio": rel,
                "rough_feature_score": rough,
                "volume_ratio": _as_float(feats.get("volume_ratio"))
                if isinstance(feats, dict)
                else None,
            }
        except Exception as exc:
            logger.warning(
                "Pre-rank feature fetch failed for %s (non-fatal): %s", key, exc
            )
            out[key] = _empty_survivor_features()
    return out


def pre_rank_score(
    ticker: str,
    *,
    prior: dict[str, Any] | None = None,
    features: dict[str, Any] | None = None,
    is_active: bool = False,
) -> float:
    """Higher is better. Deterministic; no LLM."""
    score = 0.0
    if is_active:
        score += ACTIVE_OPPORTUNITY_PRE_RANK_BONUS

    if prior:
        status = _normalize_opportunity_status(prior)
        score += _OPPORTUNITY_STATUS_PRE_RANK.get(status, 0.0)
        rr_gap = _as_float(prior.get("rr_gap"))
        if rr_gap is not None:
            score += max(0.0, 1.0 - min(rr_gap, 2.0))
        rank_sc = _as_float(prior.get("rank_score"))
        if rank_sc is not None:
            score += rank_sc * 1.5
        ta_sc = _as_float(prior.get("ta_score"))
        if ta_sc is not None:
            score += (ta_sc / 10.0) * 0.8
        sent_sc = _as_float(prior.get("sentiment_score"))
        if sent_sc is not None:
            score += (sent_sc / 10.0) * 0.5
        rr = _as_float(prior.get("current_rr")) or _as_float(prior.get("risk_reward"))
        if rr is not None:
            score += min(rr / 3.0, 1.0) * 0.4

    if features:
        rough = _as_float(features.get("rough_feature_score"))
        if rough is not None:
            score += (rough / 10.0) * 1.2
        vol = _as_float(features.get("volume_ratio"))
        if vol is not None and vol >= 1.0:
            score += min(vol - 1.0, 1.0) * 0.25
        rel = _as_float(features.get("vs_qqq_close_ratio"))
        if rel is not None and rel > 1.0:
            score += min((rel - 1.0) * 4.0, 0.8)

    return score


def pre_rank_survivors_for_analysis(
    survivors: list[str],
    *,
    cap: int,
    active_tickers: set[str] | None = None,
    prior_by_ticker: dict[str, dict[str, Any]] | None = None,
    features_by_ticker: dict[str, dict[str, Any]] | None = None,
    fetch_features: bool = False,
) -> tuple[list[str], list[str]]:
    """Rank veto survivors for the analysis pool; return (included, excluded)."""
    if not survivors:
        return [], []
    if cap <= 0:
        return [], list(survivors)

    active = active_tickers or set()
    prior = prior_by_ticker or {}
    features = features_by_ticker
    if features is None and fetch_features:
        features = compute_survivor_features(survivors, fetch_features=True)

    ranked: list[tuple[str, float]] = []
    for sym in survivors:
        key = _normalize_ticker(sym)
        if not key:
            continue
        ranked.append(
            (
                key,
                pre_rank_score(
                    key,
                    prior=prior.get(key),
                    features=(features or {}).get(key),
                    is_active=key in active,
                ),
            )
        )

    ranked.sort(key=lambda x: (-x[1], x[0]))
    included = [sym for sym, _ in ranked[: min(cap, len(ranked))]]
    excluded = [sym for sym, _ in ranked[min(cap, len(ranked)) :]]
    logger.info(
        "Analysis pre-rank: %s survivors -> %s for TA/Sentiment (cap=%s)",
        len(survivors),
        len(included),
        cap,
    )
    return included, excluded


def build_trade_pool_from_settings(
    settings: Settings,
    *,
    max_trade_pool: int | None = None,
    data_dir: Path | None = None,
) -> TradePoolResult:
    wl = load_watchlist_yaml(settings.watchlist_path())
    uni = load_universe_yaml(settings.universe_path())
    config = load_universe_pools_config(settings.universe_pools_path())
    cap = resolve_max_trade_pool(settings, max_trade_pool)
    return build_trade_pool(
        universe=uni,
        watchlist=wl,
        pools_config=config,
        max_trade_pool=cap,
        data_dir=data_dir,
    )


def format_universe_status(
    settings: Settings,
    *,
    max_tickers: int | None = None,
    max_analysis_tickers: int | None = None,
    max_cio_tickers: int | None = None,
    data_dir: Path | None = None,
) -> str:
    """Human-readable universe / pool diagnostic for CLI."""
    from swingtrade.pipeline import resolve_tier_caps

    wl = load_watchlist_yaml(settings.watchlist_path())
    uni = load_universe_yaml(settings.universe_path())
    config = load_universe_pools_config(settings.universe_pools_path())
    core = load_core_watchlist(uni, wl)
    discovery = load_discovery_candidates(config)
    gap_max = active_pullback_rr_gap_max(config)
    active_recs = load_active_opportunity_records(
        data_dir=data_dir,
        pullback_rr_gap_max=gap_max,
    )
    trade_cap = resolve_max_trade_pool(settings, max_tickers)
    analysis_cap, cio_cap = resolve_tier_caps(
        max_analysis_tickers=max_analysis_tickers,
        max_cio_tickers=max_cio_tickers,
    )
    pool = build_trade_pool(
        universe=uni,
        watchlist=wl,
        pools_config=config,
        active_records=active_recs,
        max_trade_pool=trade_cap,
        data_dir=data_dir,
    )

    watchlist_flat = {
        t for xs in wl.values() for t in (_normalize_ticker(x) for x in xs) if t
    }
    watchlist_flat -= {""}

    trade_pool_tickers = [e.ticker for e in pool.included]
    pre_rank_included, pre_rank_excluded = pre_rank_survivors_for_analysis(
        trade_pool_tickers,
        cap=analysis_cap,
        active_tickers=pool.active_tickers,
        prior_by_ticker=pool.prior_by_ticker,
        fetch_features=False,
    )

    default_cap_note = (
        "explicit --max-tickers"
        if max_tickers is not None and max_tickers > 0
        else f"default (full core={len(core)})"
    )

    lines = [
        "**Universe status**",
        f"- universe.yaml tickers: {len(uni)}",
        f"- watchlist.yaml tickers (all categories): {len(watchlist_flat)}",
        f"- core_watchlist (excl. context-only): {len(core)}",
        f"- active_opportunities (qualifying): {len(active_recs)}",
        f"- discovery_candidates (configured): {len(discovery)}",
        "",
        "**Caps**",
        f"- trade_pool_cap ({default_cap_note}): {trade_cap}",
        f"- trade_pool_count (sent to hard veto): {len(pool.included)}",
        f"- analysis_cap (TA/Sentiment): {analysis_cap}",
        f"- cio_cap: {cio_cap}",
        "",
        "**Included by source**",
    ]

    by_source: dict[str, list[str]] = {
        SOURCE_ACTIVE: [],
        SOURCE_CORE: [],
        SOURCE_DISCOVERY: [],
    }
    for entry in pool.included:
        for src in entry.sources:
            if src in by_source:
                by_source[src].append(entry.ticker)
    for src in (SOURCE_ACTIVE, SOURCE_CORE, SOURCE_DISCOVERY):
        tickers = by_source[src]
        lines.append(f"- {src}: {len(tickers)}" + (f" ({', '.join(tickers)})" if tickers else ""))

    lines.append("")
    lines.append("**Excluded before hard veto** (max_trade_pool truncation)")
    if pool.truncated:
        lines.append(f"- count: {len(pool.truncated)}")
        lines.append(f"- tickers: {', '.join(e.ticker for e in pool.truncated)}")
        active_truncated = [
            e.ticker for e in pool.truncated if SOURCE_ACTIVE in e.sources
        ]
        if active_truncated:
            lines.append(
                f"- _Warning: active opportunity truncated: {', '.join(active_truncated)}_"
            )
    else:
        lines.append("- count: 0")
        lines.append("_All merged pool tickers reach hard veto._")

    lines.append("")
    lines.append(
        "**Excluded after pre-rank** (before TA/Sentiment; assumes all trade-pool "
        "names survive hard veto)"
    )
    if pre_rank_excluded:
        lines.append(f"- count: {len(pre_rank_excluded)}")
        lines.append(f"- tickers: {', '.join(pre_rank_excluded)}")
    else:
        lines.append("- count: 0")
        lines.append("_No pre-rank exclusion at current analysis_cap._")

    lines.extend(
        [
            "",
            f"_Pre-rank status preview uses prior export scores only (fetch_features=False). "
            f"Live pipeline may add yfinance features; fetch failures are logged and non-fatal._",
        ]
    )
    return "\n".join(lines)
