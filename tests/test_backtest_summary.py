from __future__ import annotations

import csv
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from swingtrade.backtest_summary import (
    BY_RUN_CSV_COLUMNS,
    SUMMARY_CSV_COLUMNS,
    aggregate_outcome_rows,
    build_summary_tables,
    load_outcome_rows,
    run_backtest_summary,
)
from swingtrade.cli import main


def _write_outcomes(path: Path, rows: list[dict]) -> None:
    if not rows:
        raise ValueError("rows required")
    fieldnames: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _sample_row(**overrides) -> dict:
    base = {
        "date": "2026-06-05",
        "session": "pre_market",
        "run_timestamp_utc": "2026-06-05T14:33:25Z",
        "run_id": "2026-06-05_pre_market_143325Z",
        "ticker": "CAT",
        "decision": "WATCH",
        "review_level": "cio_reviewed",
        "strategy": "Momentum",
        "opportunity_status": "PULLBACK_REQUIRED",
        "exit_type": "NO_ENTRY",
        "entry_triggered": "false",
        "r_multiple": "",
    }
    base.update(overrides)
    return base


def test_single_file_summary(tmp_path: Path):
    csv_path = tmp_path / "2026-06-05_pre_market_opportunities_outcomes.csv"
    _write_outcomes(
        csv_path,
        [
            _sample_row(exit_type="WIN", entry_triggered="true", r_multiple="2.5"),
            _sample_row(
                ticker="DE",
                exit_type="LOSS",
                entry_triggered="true",
                r_multiple="-1.0",
            ),
            _sample_row(
                ticker="APP",
                exit_type="NO_ENTRY",
                entry_triggered="false",
            ),
        ],
    )
    out_summary, out_by_run, overall, by_run, loaded = run_backtest_summary(
        tmp_path,
        output_path=tmp_path / "summary.csv",
        by_run_output_path=tmp_path / "summary_by_run.csv",
    )
    assert loaded == 3
    assert out_summary.is_file()
    assert out_by_run.is_file()
    assert len(overall) >= 1
    group = overall[0]
    assert group["rows"] == 3
    assert group["valid_rows"] == 3
    assert group["wins"] == 1
    assert group["losses"] == 1
    assert group["no_entry"] == 1
    assert group["win_rate_closed"] == 50.0
    assert group["avg_r_closed"] == 0.75
    assert group["total_r_closed"] == 1.5


def test_multiple_file_summary(tmp_path: Path):
    _write_outcomes(
        tmp_path / "run_a_outcomes.csv",
        [_sample_row(exit_type="WIN", entry_triggered="true", r_multiple="1.0")],
    )
    _write_outcomes(
        tmp_path / "run_b_outcomes.csv",
        [
            _sample_row(
                date="2026-06-03",
                session="post_market",
                run_id="2026-06-03_post_market_120000Z",
                exit_type="LOSS",
                entry_triggered="true",
                r_multiple="-0.5",
            )
        ],
    )
    rows = load_outcome_rows(tmp_path)
    assert len(rows) == 2
    _out, _out2, overall, by_run, loaded = run_backtest_summary(tmp_path)
    assert loaded == 2
    assert sum(r["rows"] for r in overall) == 2
    assert len(by_run) == 2


def test_grouping_by_run_id_session_decision_opportunity_status(tmp_path: Path):
    rows = [
        _sample_row(
            decision="WATCH",
            opportunity_status="PULLBACK_REQUIRED",
            exit_type="WIN",
            entry_triggered="true",
            r_multiple="2.0",
        ),
        _sample_row(
            ticker="DE",
            decision="SCREENED",
            review_level="rank_excluded",
            opportunity_status="NEAR_BUY",
            exit_type="LOSS",
            entry_triggered="true",
            r_multiple="-1.0",
        ),
    ]
    overall = aggregate_outcome_rows(rows, group_keys=SUMMARY_CSV_COLUMNS[:5])
    assert len(overall) == 2
    watch = next(r for r in overall if r["decision"] == "WATCH")
    assert watch["opportunity_status"] == "PULLBACK_REQUIRED"
    assert watch["wins"] == 1

    by_run = aggregate_outcome_rows(rows, group_keys=BY_RUN_CSV_COLUMNS[:7])
    assert len(by_run) == 2
    assert all(r["run_id"] == "2026-06-05_pre_market_143325Z" for r in by_run)


def test_open_and_no_entry_excluded_from_closed_win_rate(tmp_path: Path):
    rows = [
        _sample_row(exit_type="WIN", entry_triggered="true", r_multiple="1.0"),
        _sample_row(ticker="DE", exit_type="OPEN", entry_triggered="true", r_multiple="0.5"),
        _sample_row(ticker="APP", exit_type="NO_ENTRY", entry_triggered="false"),
    ]
    metrics = aggregate_outcome_rows(rows, group_keys=("session",))[0]
    assert metrics["wins"] == 1
    assert metrics["open"] == 1
    assert metrics["no_entry"] == 1
    assert metrics["win_rate_closed"] == 100.0
    assert metrics["avg_r_closed"] == 1.0
    assert metrics["avg_r_triggered"] == 0.75


def test_invalid_rows_counted_but_excluded_from_performance(tmp_path: Path):
    rows = [
        _sample_row(exit_type="INVALID"),
        _sample_row(ticker="DE", exit_type="DATA_ERROR"),
        _sample_row(ticker="APP", exit_type="WIN", entry_triggered="true", r_multiple="3.0"),
    ]
    metrics = aggregate_outcome_rows(rows, group_keys=("session",))[0]
    assert metrics["rows"] == 3
    assert metrics["invalid"] == 2
    assert metrics["valid_rows"] == 1
    assert metrics["wins"] == 1
    assert metrics["avg_r_closed"] == 3.0


def test_legacy_missing_run_id_works(tmp_path: Path):
    csv_path = tmp_path / "legacy_outcomes.csv"
    _write_outcomes(
        csv_path,
        [
            {
                "date": "2026-05-21",
                "session": "pre_market",
                "ticker": "NVDA",
                "decision": "WATCH",
                "review_level": "cio_reviewed",
                "strategy": "Momentum",
                "opportunity_status": "PULLBACK_REQUIRED",
                "exit_type": "NO_ENTRY",
                "entry_triggered": "false",
            },
            {
                "date": "2026-05-21",
                "session": "pre_market",
                "run_timestamp_utc": "2026-05-21T12:00:00Z",
                "ticker": "AMD",
                "decision": "WATCH",
                "review_level": "cio_reviewed",
                "strategy": "Pullback",
                "opportunity_status": "PULLBACK_REQUIRED",
                "exit_type": "WIN",
                "entry_triggered": "true",
                "r_multiple": "1.5",
            },
        ],
    )
    _out, out_by_run, _overall, by_run, loaded = run_backtest_summary(tmp_path)
    assert loaded == 2
    with out_by_run.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    blank_run = [r for r in rows if r["run_id"] == ""]
    filled_run = [r for r in rows if r["run_id"] == "2026-05-21_pre_market_120000Z"]
    assert len(blank_run) >= 1
    assert len(filled_run) >= 1


def test_cli_summarize_backtests_help():
    try:
        main(["summarize-backtests", "--help"])
    except SystemExit as exc:
        assert exc.code == 0
    else:
        raise AssertionError("expected SystemExit(0) for --help")


if __name__ == "__main__":
    tests = [
        test_single_file_summary,
        test_multiple_file_summary,
        test_grouping_by_run_id_session_decision_opportunity_status,
        test_open_and_no_entry_excluded_from_closed_win_rate,
        test_invalid_rows_counted_but_excluded_from_performance,
        test_legacy_missing_run_id_works,
        test_cli_summarize_backtests_help,
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
