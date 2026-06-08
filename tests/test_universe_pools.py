from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from swingtrade.trade_math import (
    OPPORTUNITY_NEAR_BUY,
    OPPORTUNITY_PULLBACK_REQUIRED,
    enrich_candidate_trade_fields,
)
from swingtrade.pipeline import resolve_tier_caps
from swingtrade.settings import Settings
from swingtrade.universe_pools import (
    SOURCE_ACTIVE,
    SOURCE_CORE,
    SOURCE_DISCOVERY,
    build_trade_pool,
    build_trade_pool_from_settings,
    compute_survivor_features,
    format_universe_status,
    load_core_watchlist,
    load_discovery_seed_candidates,
    load_discovery_seed_yaml,
    max_discovery_candidates_cap,
    pre_rank_survivors_for_analysis,
    qualify_active_opportunity_records,
    resolve_default_max_trade_pool,
    resolve_max_trade_pool,
)


def _core() -> list[str]:
    return ["AAA", "BBB", "CCC", "DDD", "EEE"]


def _watchlist() -> dict[str, list[str]]:
    return {
        "Core Tech": ["AAA", "BBB"],
        "Semis": ["CCC"],
        "Context proxies": ["QQQ"],
    }


def _pullback_record(
    ticker: str,
    *,
    rr_gap: float = 0.3,
    rank_score: float = 0.7,
) -> dict:
    return enrich_candidate_trade_fields(
        {
            "ticker": ticker,
            "decision": "WATCH",
            "direction": "Long",
            "entry_zone": "100-105",
            "stop_loss": 95,
            "target": 120,
            "rank_score": rank_score,
            "ta_score": 7.0,
            "sentiment_score": 6.0,
            "rr_gap": rr_gap,
        }
    )


def test_dedupe_preserves_multiple_sources():
    active = [_pullback_record("BBB", rr_gap=0.2)]
    pool = build_trade_pool(
        universe=_core(),
        watchlist=_watchlist(),
        pools_config={"discovery_candidates": ["EEE"]},
        active_records=active,
        max_trade_pool=10,
    )
    by_ticker = {e.ticker: e.sources for e in pool.included + pool.truncated}
    assert set(by_ticker["BBB"]) == {SOURCE_ACTIVE, SOURCE_CORE}
    assert SOURCE_DISCOVERY in by_ticker["EEE"]
    assert SOURCE_CORE in by_ticker["AAA"]


def test_active_opportunities_pinned_before_core_truncation():
    active = [_pullback_record("TAIL_ACTIVE", rr_gap=0.1)]
    core = [f"T{i:02d}" for i in range(20)]
    pool = build_trade_pool(
        universe=core,
        watchlist={},
        active_records=active,
        max_trade_pool=5,
    )
    included = [e.ticker for e in pool.included]
    assert "TAIL_ACTIVE" in included
    assert len(included) == 5
    assert pool.truncated
    assert all(e.ticker != "TAIL_ACTIVE" for e in pool.truncated)


def test_max_trade_pool_respected():
    pool = build_trade_pool(
        universe=_core(),
        watchlist=_watchlist(),
        pools_config={"discovery_candidates": ["ZZZ"]},
        active_records=[],
        max_trade_pool=3,
    )
    assert len(pool.included) == 3
    assert len(pool.truncated) == len(pool.included) + len(pool.truncated) - 3


def test_pre_rank_beats_raw_yaml_order():
    survivors = ["ZZZ", "YYY", "AAA", "BBB"]
    prior = {
        "AAA": _pullback_record("AAA", rr_gap=0.1, rank_score=0.9),
    }
    yaml_order_cap, _ = survivors[:2], survivors[2:]

    ranked, excluded = pre_rank_survivors_for_analysis(
        survivors,
        cap=2,
        active_tickers={"AAA"},
        prior_by_ticker=prior,
        fetch_features=False,
    )
    assert ranked[0] == "AAA"
    assert ranked != yaml_order_cap


def test_pre_rank_lower_rr_gap_ranks_higher():
    low = enrich_candidate_trade_fields(
        {
            "ticker": "LOW",
            "entry_zone": "10-11",
            "stop_loss": 9,
            "target": 15,
        }
    )
    low["opportunity_status"] = OPPORTUNITY_PULLBACK_REQUIRED
    low["rr_gap"] = 0.1
    high = enrich_candidate_trade_fields(
        {
            "ticker": "HIGH",
            "entry_zone": "10-11",
            "stop_loss": 9,
            "target": 15,
        }
    )
    high["opportunity_status"] = OPPORTUNITY_PULLBACK_REQUIRED
    high["rr_gap"] = 0.9
    prior = {"LOW": low, "HIGH": high}
    ranked, _ = pre_rank_survivors_for_analysis(
        ["HIGH", "LOW"],
        cap=2,
        prior_by_ticker=prior,
        fetch_features=False,
    )
    assert ranked[0] == "LOW"


def test_qualify_active_opportunity_statuses():
    near = enrich_candidate_trade_fields(
        {
            "ticker": "X",
            "entry_zone": "10",
            "stop_loss": 9,
            "target": 15,
        }
    )
    near["opportunity_status"] = OPPORTUNITY_NEAR_BUY
    tight_pullback = _pullback_record("Y", rr_gap=0.4)
    tight_pullback["rr_gap"] = 0.4
    wide_pullback = _pullback_record("Z", rr_gap=1.5)
    wide_pullback["rr_gap"] = 1.5
    qualified = qualify_active_opportunity_records(
        [near, tight_pullback, wide_pullback],
        pullback_rr_gap_max=1.0,
    )
    tickers = {r["ticker"] for r in qualified}
    assert tickers == {"X", "Y"}
    assert "Z" not in tickers


def test_load_active_from_opportunity_csv(tmp_path: Path):
    row = enrich_candidate_trade_fields(
        {
            "ticker": "AMD",
            "opportunity_status": OPPORTUNITY_PULLBACK_REQUIRED,
            "rr_gap": 0.37,
            "entry_zone": "471-490",
            "stop_loss": 460,
            "target": 554,
        }
    )
    csv_path = tmp_path / "opportunities" / "2026-06-05_pre_market_opportunities.csv"
    csv_path.parent.mkdir(parents=True)
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(row.keys()))
        writer.writeheader()
        writer.writerow({k: str(v) for k, v in row.items()})

    pool = build_trade_pool(
        universe=["NVDA"],
        watchlist={},
        active_records=None,
        max_trade_pool=10,
        data_dir=tmp_path,
    )
    tickers = [e.ticker for e in pool.included]
    assert "AMD" in tickers
    assert SOURCE_ACTIVE in next(e for e in pool.included if e.ticker == "AMD").sources


def test_load_active_from_jsonl(tmp_path: Path):
    jsonl = tmp_path / "candidates" / "2026-06-05_pre_market.jsonl"
    jsonl.parent.mkdir(parents=True)
    record = _pullback_record("MSFT", rr_gap=0.5)
    record["run_timestamp_utc"] = "2026-06-05T10:00:00Z"
    with jsonl.open("w", encoding="utf-8") as f:
        f.write(json.dumps(record))
        f.write("\n")

    pool = build_trade_pool(
        universe=["NVDA"],
        watchlist={},
        max_trade_pool=10,
        data_dir=tmp_path,
    )
    assert "MSFT" in [e.ticker for e in pool.included]


def test_core_watchlist_excludes_context_only():
    wl = {
        "Core Tech": ["NVDA"],
        "Context proxies": ["QQQ", "ONLY_CTX"],
    }
    core = load_core_watchlist(["NVDA", "ONLY_CTX"], wl)
    assert "NVDA" in core
    assert "QQQ" not in core
    assert "ONLY_CTX" not in core


def test_discovery_seed_loads_from_config():
    repo = Path(__file__).parent.parent
    settings = Settings(swingtrade_config_dir=repo / "config")
    seeds = load_discovery_seed_yaml(settings.discovery_seed_path())
    assert len(seeds) >= 15
    assert "UNH" in seeds
    assert "JPM" in seeds
    from swingtrade.universe_pools import load_universe_pools_config

    config = load_universe_pools_config(settings.universe_pools_path())
    capped = load_discovery_seed_candidates(settings=settings)
    assert len(capped) == max_discovery_candidates_cap(config)
    assert len(capped) == 0


def test_discovery_duplicate_with_core_preserves_source_labels():
    pool = build_trade_pool(
        universe=["AAA", "BBB"],
        watchlist={},
        pools_config={
            "limits": {"max_discovery_candidates": 5},
            "discovery_candidates": ["BBB", "DIS1", "DIS2"],
        },
        active_records=[],
        max_trade_pool=10,
    )
    bbb = next(e for e in pool.included if e.ticker == "BBB")
    assert SOURCE_CORE in bbb.sources
    assert SOURCE_DISCOVERY in bbb.sources
    dis1 = next(e for e in pool.included if e.ticker == "DIS1")
    assert dis1.sources == [SOURCE_DISCOVERY]


def test_max_discovery_zero_excludes_discovery_from_trade_pool():
    pool = build_trade_pool(
        universe=_core(),
        watchlist=_watchlist(),
        pools_config={
            "limits": {"max_discovery_candidates": 0, "min_core_slots": 41},
            "discovery_candidates": ["Z1", "Z2", "Z3"],
        },
        active_records=[],
        max_trade_pool=50,
    )
    discovery_only = [
        e.ticker
        for e in pool.included
        if SOURCE_DISCOVERY in e.sources and SOURCE_CORE not in e.sources
    ]
    assert discovery_only == []
    assert pool.discovery_included_count == 0
    assert len(pool.included) == len(_core())


def test_max_discovery_candidates_cap_respected():
    pool = build_trade_pool(
        universe=_core(),
        watchlist=_watchlist(),
        pools_config={
            "limits": {"max_discovery_candidates": 2},
            "discovery_candidates": ["Z1", "Z2", "Z3", "Z4"],
        },
        active_records=[],
        max_trade_pool=50,
    )
    discovery_included = [
        e.ticker
        for e in pool.included
        if SOURCE_DISCOVERY in e.sources and SOURCE_CORE not in e.sources
    ]
    assert len(discovery_included) == 2


def test_universe_status_reports_discovery_counts():
    repo = Path(__file__).parent.parent
    settings = Settings(swingtrade_config_dir=repo / "config")
    text = format_universe_status(settings, max_tickers=None)
    assert "discovery_seed" in text
    assert "discovery_included" in text
    assert "core_watchlist" in text
    assert "active_opportunities" in text
    assert "Included tickers (source labels)" in text


def test_default_trade_pool_includes_full_core_universe():
    from swingtrade.universe_loader import load_universe_yaml
    from swingtrade.watchlist_store import load_watchlist_yaml

    repo = Path(__file__).parent.parent
    settings = Settings(swingtrade_config_dir=repo / "config")
    core = load_core_watchlist(
        load_universe_yaml(settings.universe_path()),
        load_watchlist_yaml(settings.watchlist_path()),
    )
    assert len(core) == 41
    discovery_only = [
        t
        for t in load_discovery_seed_candidates(settings=settings)
        if t not in set(core)
    ]
    assert len(discovery_only) == 0
    expected_default = len(core)
    assert resolve_default_max_trade_pool(settings) == expected_default
    pool = build_trade_pool_from_settings(settings, max_trade_pool=None)
    assert len(pool.included) == expected_default
    assert pool.discovery_included_count == 0
    assert len(pool.truncated) == 0
    assert resolve_max_trade_pool(settings, None) == expected_default


def test_analysis_cap_still_limits_downstream_pool():
    survivors = [f"T{i:02d}" for i in range(41)]
    included, excluded = pre_rank_survivors_for_analysis(
        survivors,
        cap=30,
        fetch_features=False,
    )
    assert len(included) == 30
    assert len(excluded) == 11


def test_tier_caps_defaults_unchanged():
    analysis, cio = resolve_tier_caps(
        max_analysis_tickers=None,
        max_cio_tickers=None,
        max_downstream_tickers=None,
    )
    assert analysis == 30
    assert cio == 12


def test_pre_rank_yfinance_failure_is_non_fatal():
    import swingtrade.universe_pools as up

    def _boom(_sym: str):
        raise OSError("network down")

    original = up.ohlcv_for_ticker
    up.ohlcv_for_ticker = _boom
    try:
        features = compute_survivor_features(["AAA", "BBB"], fetch_features=True)
        assert "AAA" in features
        assert "BBB" in features
        ranked, excluded = pre_rank_survivors_for_analysis(
            ["AAA", "BBB", "CCC"],
            cap=2,
            features_by_ticker=features,
            fetch_features=False,
        )
        assert len(ranked) == 2
        assert len(excluded) == 1
    finally:
        up.ohlcv_for_ticker = original


def test_build_trade_pool_survives_feature_fetch_errors():
    import swingtrade.universe_pools as up

    def _boom(_sym: str):
        raise RuntimeError("yfinance unavailable")

    original = up.ohlcv_for_ticker
    up.ohlcv_for_ticker = _boom
    try:
        pool = build_trade_pool(universe=_core(), watchlist=_watchlist(), max_trade_pool=5)
        assert len(pool.included) == 5
        features = compute_survivor_features(["AAA"], fetch_features=True)
        assert features["AAA"]["rough_feature_score"] is None
    finally:
        up.ohlcv_for_ticker = original


def test_no_llm_imports_in_universe_pools_module():
    import swingtrade.universe_pools as mod

    source = Path(mod.__file__).read_text(encoding="utf-8")
    assert "anthropic" not in source.lower()
    assert "complete_json_agent" not in source


if __name__ == "__main__":
    tests = [
        test_discovery_seed_loads_from_config,
        test_discovery_duplicate_with_core_preserves_source_labels,
        test_max_discovery_zero_excludes_discovery_from_trade_pool,
        test_max_discovery_candidates_cap_respected,
        test_universe_status_reports_discovery_counts,
        test_default_trade_pool_includes_full_core_universe,
        test_analysis_cap_still_limits_downstream_pool,
        test_tier_caps_defaults_unchanged,
        test_pre_rank_yfinance_failure_is_non_fatal,
        test_build_trade_pool_survives_feature_fetch_errors,
        test_dedupe_preserves_multiple_sources,
        test_active_opportunities_pinned_before_core_truncation,
        test_max_trade_pool_respected,
        test_pre_rank_beats_raw_yaml_order,
        test_pre_rank_lower_rr_gap_ranks_higher,
        test_qualify_active_opportunity_statuses,
        test_load_active_from_opportunity_csv,
        test_load_active_from_jsonl,
        test_core_watchlist_excludes_context_only,
        test_no_llm_imports_in_universe_pools_module,
    ]
    failed = 0
    for t in tests:
        try:
            if t.__name__.endswith("_csv") or t.__name__.endswith("_jsonl"):
                import tempfile

                with tempfile.TemporaryDirectory() as tmp:
                    t(Path(tmp))
            else:
                t()
            print(f"PASS {t.__name__}")
        except Exception as e:
            failed += 1
            print(f"FAIL {t.__name__}: {e}")
    raise SystemExit(1 if failed else 0)
