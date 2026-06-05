from __future__ import annotations

import csv
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from swingtrade.backtest_summary import (
    BY_RUN_CSV_COLUMNS,
    SUMMARY_CSV_COLUMNS,
    SUMMARY_GROUP_KEYS,
    BacktestSummaryFilters,
    aggregate_outcome_rows,
    apply_summary_filters,
    is_legacy_outcome_row,
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
        "planned_entry_price": "910.0",
        "conditional_buy_limit": "true",
        "resolved_entry_price": "910.0",
        "stop_loss": "890.0",
        "target": "960.0",
        "exit_type": "NO_ENTRY",
        "entry_triggered": "false",
        "r_multiple": "",
    }
    base.update(overrides)
    return base


def _write_fixture_dir(tmp_path: Path, rows: list[dict]) -> None:
    _write_outcomes(tmp_path / "fixture_outcomes.csv", rows)


def test_single_file_summary(tmp_path: Path):
    _write_fixture_dir(
        tmp_path,
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
    out_summary, out_by_run, overall, by_run, raw, filtered = run_backtest_summary(
        tmp_path,
        output_path=tmp_path / "summary.csv",
        by_run_output_path=tmp_path / "summary_by_run.csv",
    )
    assert raw == 3
    assert filtered == 3
    assert out_summary.is_file()
    assert out_by_run.is_file()
    group = overall[0]
    assert group["rows"] == 3
    assert group["valid_rows"] == 3
    assert group["wins"] == 1
    assert group["losses"] == 1
    assert group["no_entry"] == 1
    assert group["win_rate_closed"] == 50.0
    assert group["avg_r_closed"] == 0.75
    assert group["total_r_closed"] == 1.5
    assert group["entry_rate"] == round(2 / 3, 4)
    assert group["closed_rate"] == 1.0


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
                run_timestamp_utc="2026-06-03T12:00:00Z",
                exit_type="LOSS",
                entry_triggered="true",
                r_multiple="-0.5",
            )
        ],
    )
    rows = load_outcome_rows(tmp_path)
    assert len(rows) == 2
    _out, _out2, overall, by_run, raw, filtered = run_backtest_summary(tmp_path)
    assert raw == 2
    assert filtered == 2
    assert sum(r["rows"] for r in overall) == 2
    assert len(by_run) == 2


def test_grouping_by_run_id_session_decision_opportunity_status():
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
    overall = aggregate_outcome_rows(rows, group_keys=SUMMARY_GROUP_KEYS)
    assert len(overall) == 2
    watch = next(r for r in overall if r["decision"] == "WATCH")
    assert watch["opportunity_status"] == "PULLBACK_REQUIRED"
    assert watch["wins"] == 1


def test_open_and_no_entry_excluded_from_closed_win_rate():
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
    assert metrics["no_entry_rate"] == round(1 / 3, 4)
    assert metrics["open_rate"] == round(1 / 2, 4)


def test_invalid_rows_counted_but_excluded_from_performance():
    rows = [
        _sample_row(exit_type="INVALID"),
        _sample_row(ticker="DE", exit_type="DATA_ERROR"),
        _sample_row(ticker="APP", exit_type="WIN", entry_triggered="true", r_multiple="3.0"),
    ]
    metrics = aggregate_outcome_rows(rows, group_keys=("session",))[0]
    assert metrics["rows"] == 3
    assert metrics["invalid_rows"] == 2
    assert metrics["invalid"] == 2
    assert metrics["valid_rows"] == 1
    assert metrics["wins"] == 1
    assert metrics["avg_r_closed"] == 3.0


def test_since_until_filtering():
    rows = [
        _sample_row(date="2026-06-01"),
        _sample_row(ticker="DE", date="2026-06-05"),
        _sample_row(ticker="APP", date="2026-06-10"),
    ]
    filt = BacktestSummaryFilters(since="2026-06-05", until="2026-06-05")
    out = apply_summary_filters(rows, filt)
    assert len(out) == 1
    assert out[0]["ticker"] == "DE"


def test_session_filtering():
    rows = [
        _sample_row(session="pre_market"),
        _sample_row(ticker="DE", session="post_market", run_id="2026-06-05_post_market_120000Z"),
    ]
    out = apply_summary_filters(rows, BacktestSummaryFilters(session="post_market"))
    assert len(out) == 1
    assert out[0]["ticker"] == "DE"


def test_decision_filtering():
    rows = [
        _sample_row(decision="WATCH"),
        _sample_row(ticker="DE", decision="PASS"),
    ]
    out = apply_summary_filters(rows, BacktestSummaryFilters(decision="PASS"))
    assert len(out) == 1
    assert out[0]["decision"] == "PASS"


def test_opportunity_status_filtering():
    rows = [
        _sample_row(opportunity_status="PULLBACK_REQUIRED"),
        _sample_row(ticker="DE", opportunity_status="NEAR_BUY"),
    ]
    out = apply_summary_filters(
        rows, BacktestSummaryFilters(opportunity_status="NEAR_BUY")
    )
    assert len(out) == 1
    assert out[0]["ticker"] == "DE"


def test_only_closed_excludes_open_and_no_entry():
    rows = [
        _sample_row(exit_type="WIN", entry_triggered="true"),
        _sample_row(ticker="DE", exit_type="OPEN", entry_triggered="true"),
        _sample_row(ticker="APP", exit_type="NO_ENTRY"),
    ]
    out = apply_summary_filters(rows, BacktestSummaryFilters(only_closed=True))
    assert len(out) == 1
    assert out[0]["exit_type"] == "WIN"


def test_only_conditional_filters_true():
    rows = [
        _sample_row(conditional_buy_limit="true"),
        _sample_row(ticker="DE", conditional_buy_limit="false"),
    ]
    out = apply_summary_filters(rows, BacktestSummaryFilters(only_conditional=True))
    assert len(out) == 1
    assert out[0]["ticker"] == "CAT"


def test_only_planned_filters_planned_entry_price():
    rows = [
        _sample_row(planned_entry_price="910.0"),
        _sample_row(ticker="DE", planned_entry_price=""),
    ]
    out = apply_summary_filters(rows, BacktestSummaryFilters(only_planned=True))
    assert len(out) == 1
    assert out[0]["ticker"] == "CAT"


def test_exclude_legacy_removes_rows_without_modern_tracking():
    modern = _sample_row()
    legacy = {
        "date": "2026-05-21",
        "session": "pre_market",
        "ticker": "OLD",
        "decision": "WATCH",
        "exit_type": "NO_ENTRY",
    }
    assert is_legacy_outcome_row(legacy)
    assert not is_legacy_outcome_row(modern)
    out = apply_summary_filters([modern, legacy], BacktestSummaryFilters(exclude_legacy=True))
    assert len(out) == 1
    assert out[0]["ticker"] == "CAT"


def test_rate_calculations():
    rows = [
        _sample_row(exit_type="WIN", entry_triggered="true", r_multiple="2.0"),
        _sample_row(ticker="DE", exit_type="LOSS", entry_triggered="true", r_multiple="-1.0"),
        _sample_row(ticker="APP", exit_type="NO_ENTRY", entry_triggered="false"),
        _sample_row(ticker="X", exit_type="OPEN", entry_triggered="true", r_multiple="0.5"),
    ]
    m = aggregate_outcome_rows(rows, group_keys=("session",))[0]
    assert m["entry_rate"] == 0.75
    assert m["closed_rate"] == round(2 / 3, 4)
    assert m["no_entry_rate"] == 0.25
    assert m["open_rate"] == round(1 / 3, 4)


def test_zero_row_filter_writes_empty_csv(tmp_path: Path):
    _write_fixture_dir(tmp_path, [_sample_row()])
    filt = BacktestSummaryFilters(decision="BUY")
    out_summary, out_by_run, overall, by_run, raw, filtered = run_backtest_summary(
        tmp_path,
        output_path=tmp_path / "summary.csv",
        by_run_output_path=tmp_path / "summary_by_run.csv",
        filters=filt,
    )
    assert raw == 1
    assert filtered == 0
    assert overall == []
    assert by_run == []
    assert out_summary.is_file()
    with out_summary.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        assert list(reader.fieldnames) == list(SUMMARY_CSV_COLUMNS)
        assert list(reader) == []


def test_filter_label_in_output(tmp_path: Path):
    _write_fixture_dir(tmp_path, [_sample_row()])
    filt = BacktestSummaryFilters(session="pre_market", only_conditional=True)
    _out, _out2, overall, _by_run, _raw, _filtered = run_backtest_summary(
        tmp_path, filters=filt
    )
    assert overall[0]["filter_label"] == filt.label()


def test_legacy_missing_run_id_works(tmp_path: Path):
    _write_outcomes(
        tmp_path / "legacy_outcomes.csv",
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
                "planned_entry_price": "100",
                "exit_type": "WIN",
                "entry_triggered": "true",
                "r_multiple": "1.5",
                "resolved_entry_price": "100",
                "stop_loss": "95",
                "target": "110",
            },
        ],
    )
    _out, out_by_run, _overall, _by_run, raw, filtered = run_backtest_summary(tmp_path)
    assert raw == 2
    assert filtered == 2
    with out_by_run.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert any(r["run_id"] == "" for r in rows)
    assert any(r["run_id"] == "2026-05-21_pre_market_120000Z" for r in rows)


def test_cli_summarize_backtests_help():
    try:
        main(["summarize-backtests", "--help"])
    except SystemExit as exc:
        assert exc.code == 0
    else:
        raise AssertionError("expected SystemExit(0) for --help")


def test_cli_help_lists_filter_flags():
    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            main(["summarize-backtests", "--help"])
    except SystemExit as exc:
        assert exc.code == 0
    captured = buf.getvalue()
    for flag in (
        "--exclude-legacy",
        "--only-closed",
        "--only-conditional",
        "--only-planned",
        "--since",
        "--opportunity-status",
    ):
        assert flag in captured


if __name__ == "__main__":
    tests = [
        test_single_file_summary,
        test_multiple_file_summary,
        test_grouping_by_run_id_session_decision_opportunity_status,
        test_open_and_no_entry_excluded_from_closed_win_rate,
        test_invalid_rows_counted_but_excluded_from_performance,
        test_since_until_filtering,
        test_session_filtering,
        test_decision_filtering,
        test_opportunity_status_filtering,
        test_only_closed_excludes_open_and_no_entry,
        test_only_conditional_filters_true,
        test_only_planned_filters_planned_entry_price,
        test_exclude_legacy_removes_rows_without_modern_tracking,
        test_rate_calculations,
        test_zero_row_filter_writes_empty_csv,
        test_filter_label_in_output,
        test_legacy_missing_run_id_works,
        test_cli_summarize_backtests_help,
        test_cli_help_lists_filter_flags,
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
