from __future__ import annotations

import csv
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from swingtrade.cli import main
from swingtrade.research_diagnostics import (
    _accumulate,
    build_diagnostics_rows,
    confidence_label,
    run_research_edge_diagnostics,
)


def _signal_row(**overrides) -> dict:
    base = {
        "date": "2026-06-08",
        "session": "pre_market",
        "run_id": "2026-06-08_pre_market_133641Z",
        "ticker": "NVDA",
        "source_type": "opportunity",
        "decision": "WATCH",
        "review_level": "cio_reviewed",
        "opportunity_status": "NEAR_BUY",
        "strategy": "Pullback",
        "market_regime": "bull",
        "has_outcome": "true",
        "entry_triggered": "true",
        "exit_type": "WIN",
        "r_multiple": "2.0",
        "stop_loss": "95",
        "target": "110",
        "planned_entry_price": "98",
        "conditional_buy_limit": "false",
        "math_valid": "true",
    }
    base.update(overrides)
    return base


def _write_signal_master(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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


def test_closed_only_win_rate_and_expectancy():
    rows = [
        _signal_row(ticker="A", exit_type="WIN", r_multiple="2.0"),
        _signal_row(ticker="B", exit_type="LOSS", r_multiple="-1.0"),
        _signal_row(ticker="C", exit_type="OPEN", r_multiple="0.5"),
        _signal_row(ticker="D", exit_type="NO_ENTRY", entry_triggered="false"),
    ]
    acc = _accumulate(rows)
    assert acc.wins == 1
    assert acc.losses == 1
    assert acc.open_count == 1
    assert acc.no_entry == 1
    record = acc.to_record(
        report_section="overall",
        dimension="overall",
        dimension_value="all",
        rows=rows,
    )
    assert record["closed_trades"] == 2
    assert record["win_rate_closed"] == 50.0
    assert record["expectancy_closed"] == 0.5
    assert record["total_r_closed"] == 1.0


def test_open_and_no_entry_excluded_from_closed_performance():
    rows = [
        _signal_row(ticker="A", exit_type="OPEN", r_multiple="1.5"),
        _signal_row(ticker="B", exit_type="NO_ENTRY", entry_triggered="false"),
    ]
    record = _accumulate(rows).to_record(
        report_section="overall",
        dimension="overall",
        dimension_value="all",
        rows=rows,
    )
    assert record["closed_trades"] == 0
    assert record["win_rate_closed"] is None
    assert record["expectancy_closed"] is None
    assert record["entries_triggered"] == 1
    assert record["no_entry"] == 1


def test_invalid_and_data_error_excluded_from_performance():
    rows = [
        _signal_row(ticker="A", exit_type="INVALID"),
        _signal_row(ticker="B", exit_type="DATA_ERROR"),
        _signal_row(ticker="C", exit_type="WIN", r_multiple="2.0"),
    ]
    acc = _accumulate(rows)
    assert acc.invalid == 1
    assert acc.data_error == 1
    assert acc.valid_rows == 1
    record = acc.to_record(
        report_section="overall",
        dimension="overall",
        dimension_value="all",
        rows=rows,
    )
    assert record["closed_trades"] == 1
    assert record["wins"] == 1


def test_grouping_by_decision_and_opportunity_status():
    rows = [
        _signal_row(ticker="A", decision="BUY", opportunity_status="BUY_NOW"),
        _signal_row(ticker="B", decision="PASS", opportunity_status="NO_ACTIONABLE_ZONE"),
    ]
    diagnostics = build_diagnostics_rows(rows)
    buy = next(
        r
        for r in diagnostics
        if r["report_section"] == "group"
        and r["dimension"] == "decision"
        and r["dimension_value"] == "BUY"
    )
    no_zone = next(
        r
        for r in diagnostics
        if r["report_section"] == "group"
        and r["dimension"] == "opportunity_status"
        and r["dimension_value"] == "NO_ACTIONABLE_ZONE"
    )
    assert buy["rows"] == 1
    assert no_zone["rows"] == 1


def test_confidence_label_thresholds():
    assert confidence_label(0) == "LOW"
    assert confidence_label(19) == "LOW"
    assert confidence_label(20) == "EARLY"
    assert confidence_label(49) == "EARLY"
    assert confidence_label(50) == "DEVELOPING"
    assert confidence_label(99) == "DEVELOPING"
    assert confidence_label(100) == "MORE_RELIABLE"


def test_low_sample_warning_in_summary(tmp_path: Path):
    rows = [
        _signal_row(
            ticker=f"T{i}",
            exit_type="WIN" if i % 2 == 0 else "LOSS",
            r_multiple="1.0" if i % 2 == 0 else "-1.0",
        )
        for i in range(10)
    ]
    signal_path = tmp_path / "signal_master.csv"
    _write_signal_master(signal_path, rows)
    summary, _ = run_research_edge_diagnostics(
        input_path=signal_path,
        output_csv=tmp_path / "edge.csv",
        output_markdown=tmp_path / "edge.md",
    )
    assert summary.confidence == "LOW"
    assert any("LOW confidence" in w for w in summary.warnings)


def test_cli_help_includes_diagnose_research_edge():
    try:
        main(["diagnose-research-edge", "--help"])
    except SystemExit as exc:
        assert exc.code == 0


def test_cli_writes_outputs(tmp_path: Path):
    _write_signal_master(
        tmp_path / "signal_master.csv",
        [_signal_row()],
    )
    code = main(
        [
            "diagnose-research-edge",
            "--input",
            str(tmp_path / "signal_master.csv"),
            "--output",
            str(tmp_path / "edge.csv"),
            "--markdown",
            str(tmp_path / "edge.md"),
        ]
    )
    assert code == 0
    assert (tmp_path / "edge.csv").is_file()
    assert (tmp_path / "edge.md").is_file()


if __name__ == "__main__":
    tests = [
        test_closed_only_win_rate_and_expectancy,
        test_open_and_no_entry_excluded_from_closed_performance,
        test_invalid_and_data_error_excluded_from_performance,
        test_grouping_by_decision_and_opportunity_status,
        test_confidence_label_thresholds,
        test_low_sample_warning_in_summary,
        test_cli_help_includes_diagnose_research_edge,
        test_cli_writes_outputs,
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
