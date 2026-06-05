from __future__ import annotations

import csv
import sys
import tempfile
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from swingtrade.cli import main
from swingtrade.outcome_tracker import (
    EXIT_DATA_ERROR,
    EXIT_INVALID,
    EXIT_LOSS,
    EXIT_NO_ENTRY,
    EXIT_OPEN,
    EXIT_WIN,
    evaluate_signal_row,
    format_summary_text,
    resolve_entry_price,
    run_outcome_backtest,
    simulate_long_outcome,
    summarize_outcomes,
)


def _bars(rows: list[tuple[str, float, float, float, float]]) -> pd.DataFrame:
    idx = pd.to_datetime([r[0] for r in rows])
    return pd.DataFrame(
        {
            "Open": [r[1] for r in rows],
            "High": [r[2] for r in rows],
            "Low": [r[3] for r in rows],
            "Close": [r[4] for r in rows],
        },
        index=idx,
    )


def _signal_row(**overrides) -> dict:
    base = {
        "date": "2026-01-06",
        "session": "pre_market",
        "ticker": "TEST",
        "decision": "WATCH",
        "review_level": "cio_reviewed",
        "analysis_rank": "1",
        "rank_score": "0.7",
        "direction": "Long",
        "strategy": "Momentum",
        "opportunity_status": "PULLBACK_REQUIRED",
        "current_rr": "1.0",
        "valid_entry_max": "99.0",
        "planned_entry_price": "98.0",
        "planned_entry_rr": "2.5",
        "conditional_buy_limit": "true",
        "entry_zone": "98-100",
        "stop_loss": "95",
        "target": "110",
        "market_regime": "bull",
        "tech_bias": "favorable",
        "overall_risk_level": "Low",
    }
    base.update(overrides)
    return base


def test_entry_triggers_target_before_stop_is_win():
    bars = _bars(
        [
            ("2026-01-06", 100, 101, 97, 99),
            ("2026-01-07", 99, 112, 98, 111),
            ("2026-01-08", 111, 112, 110, 111),
        ]
    )
    out = simulate_long_outcome(
        bars,
        signal_date=date(2026, 1, 6),
        entry_price=98.0,
        stop_loss=95.0,
        target=110.0,
        horizon_days=3,
    )
    assert out["entry_triggered"] is True
    assert out["exit_type"] == EXIT_WIN
    assert out["exit_first"] == "TARGET"
    assert float(out["exit_price"]) == 110.0
    assert float(out["pnl_per_share"]) == 12.0


def test_entry_triggers_stop_before_target_is_loss():
    bars = _bars(
        [
            ("2026-01-06", 100, 100, 97.5, 98),
            ("2026-01-07", 98, 99, 94, 96),
            ("2026-01-08", 96, 97, 95, 96),
        ]
    )
    out = simulate_long_outcome(
        bars,
        signal_date=date(2026, 1, 6),
        entry_price=98.0,
        stop_loss=95.0,
        target=110.0,
        horizon_days=3,
    )
    assert out["exit_type"] == EXIT_LOSS
    assert out["exit_first"] == "STOP"
    assert float(out["exit_price"]) == 95.0
    assert float(out["pnl_per_share"]) == -3.0


def test_price_never_touches_entry_is_no_entry():
    bars = _bars(
        [
            ("2026-01-06", 102, 103, 101, 102),
            ("2026-01-07", 102, 103, 101.5, 102),
            ("2026-01-08", 102, 103, 101.8, 102),
        ]
    )
    out = simulate_long_outcome(
        bars,
        signal_date=date(2026, 1, 6),
        entry_price=98.0,
        stop_loss=95.0,
        target=110.0,
        horizon_days=3,
    )
    assert out["entry_triggered"] is False
    assert out["exit_type"] == EXIT_NO_ENTRY


def test_entry_without_stop_or_target_hit_is_open():
    bars = _bars(
        [
            ("2026-01-06", 100, 100, 97, 99),
            ("2026-01-07", 99, 104, 98, 103),
            ("2026-01-08", 103, 105, 102, 104),
        ]
    )
    out = simulate_long_outcome(
        bars,
        signal_date=date(2026, 1, 6),
        entry_price=98.0,
        stop_loss=95.0,
        target=110.0,
        horizon_days=3,
    )
    assert out["entry_triggered"] is True
    assert out["exit_type"] == EXIT_OPEN
    assert float(out["exit_price"]) == 104.0


def test_missing_geometry_is_invalid():
    row = _signal_row(stop_loss="", target="")
    out = evaluate_signal_row(row, horizon_days=5, fetch_ohlcv=lambda *_: pd.DataFrame())
    assert out["exit_type"] == EXIT_INVALID


def test_planned_entry_preferred_over_valid_entry_max():
    row = _signal_row(planned_entry_price="98.0", valid_entry_max="99.0", entry_zone="100-102")
    assert resolve_entry_price(row) == 98.0


def test_summary_counts_are_correct():
    rows = [
        {"exit_type": EXIT_WIN, "entry_triggered": True, "r_multiple": 2.0, "decision": "WATCH", "opportunity_status": "PULLBACK_REQUIRED", "review_level": "cio_reviewed"},
        {"exit_type": EXIT_LOSS, "entry_triggered": True, "r_multiple": -1.0, "decision": "WATCH", "opportunity_status": "PULLBACK_REQUIRED", "review_level": "cio_reviewed"},
        {"exit_type": EXIT_NO_ENTRY, "entry_triggered": False, "decision": "WATCH", "opportunity_status": "PULLBACK_REQUIRED", "review_level": "cio_reviewed"},
        {"exit_type": EXIT_OPEN, "entry_triggered": True, "r_multiple": 0.5, "decision": "PASS", "opportunity_status": "NEAR_BUY", "review_level": "cio_reviewed"},
        {"exit_type": EXIT_INVALID, "entry_triggered": False},
        {"exit_type": EXIT_DATA_ERROR, "entry_triggered": False},
    ]
    summary, extras = summarize_outcomes(rows)
    assert summary.total_rows == 6
    assert summary.valid_rows == 4
    assert summary.invalid_rows == 1
    assert summary.data_error_rows == 1
    assert summary.entries_triggered == 3
    assert summary.wins == 1
    assert summary.losses == 1
    assert summary.no_entries == 1
    assert summary.open_trades == 1
    assert summary.win_rate == 50.0
    assert extras["average_r_triggered"] == 0.5
    text = format_summary_text(summary, extras)
    assert "wins: 1" in text
    assert "average R (triggered): 0.5" in text


def test_run_outcome_backtest_with_mock_fetch(tmp_path: Path):
    csv_path = tmp_path / "signals.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(_signal_row().keys()))
        writer.writeheader()
        writer.writerow(_signal_row())

    def mock_fetch(symbol: str, start: date, end: date) -> pd.DataFrame:
        return _bars(
            [
                ("2026-01-06", 100, 100, 97, 99),
                ("2026-01-07", 99, 112, 98, 111),
            ]
        )

    out_path, results, summary, extras = run_outcome_backtest(
        csv_path,
        horizon_days=2,
        output_path=tmp_path / "out.csv",
        fetch_ohlcv=mock_fetch,
    )
    assert out_path.is_file()
    assert results[0]["exit_type"] == EXIT_WIN
    assert summary.wins == 1


def test_cli_backtest_outcomes_wires():
    try:
        main(["backtest-outcomes", "--help"])
    except SystemExit as exc:
        assert exc.code == 0
    else:
        raise AssertionError("expected SystemExit(0) for --help")


if __name__ == "__main__":
    tests = [
        test_entry_triggers_target_before_stop_is_win,
        test_entry_triggers_stop_before_target_is_loss,
        test_price_never_touches_entry_is_no_entry,
        test_entry_without_stop_or_target_hit_is_open,
        test_missing_geometry_is_invalid,
        test_planned_entry_preferred_over_valid_entry_max,
        test_summary_counts_are_correct,
        test_run_outcome_backtest_with_mock_fetch,
        test_cli_backtest_outcomes_wires,
    ]
    failed = 0
    for t in tests:
        try:
            if "tmp_path" in t.__code__.co_varnames:
                with tempfile.TemporaryDirectory() as td:
                    t(Path(td))
            else:
                t()
            print(f"PASS {t.__name__}")
        except Exception as e:
            failed += 1
            print(f"FAIL {t.__name__}: {e}")
    raise SystemExit(1 if failed else 0)
