from __future__ import annotations

import csv
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from swingtrade.cli import main
from swingtrade.research_dataset import (
    build_signal_master,
    dedupe_master_rows,
    outcome_source_type_from_filename,
    run_build_research_dataset,
)


def _write_csv(path: Path, rows: list[dict]) -> None:
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


def _review_row(**overrides) -> dict:
    base = {
        "date": "2026-06-08",
        "session": "pre_market",
        "run_timestamp_utc": "2026-06-08T13:36:41Z",
        "run_id": "2026-06-08_pre_market_133641Z",
        "ticker": "NVDA",
        "review_level": "cio_reviewed",
        "analysis_rank": "2",
        "rank_score": "0.8",
        "decision": "PASS",
        "direction": "Long",
        "strategy": "Momentum",
        "math_valid": "true",
        "stop_loss": "95",
        "target": "110",
        "risk_reward": "2.0",
    }
    base.update(overrides)
    return base


def _opportunity_row(**overrides) -> dict:
    return _review_row(decision="WATCH", opportunity_status="NEAR_BUY", **overrides)


def _outcome_row(**overrides) -> dict:
    base = {
        "date": "2026-06-08",
        "session": "pre_market",
        "run_timestamp_utc": "2026-06-08T13:36:41Z",
        "run_id": "2026-06-08_pre_market_133641Z",
        "ticker": "NVDA",
        "entry_triggered": "true",
        "exit_type": "WIN",
        "entry_date": "2026-06-09",
        "exit_date": "2026-06-10",
        "exit_price": "110",
        "r_multiple": "2.5",
        "max_favourable_excursion": "12",
        "max_adverse_excursion": "1",
        "bars_held": "2",
        "outcome_note": "target hit",
    }
    base.update(overrides)
    return base


def _fixture_tree(root: Path) -> None:
    _write_csv(
        root / "reviews" / "archive" / "2026-06-08_pre_market_133641Z_review.csv",
        [
            _review_row(ticker="NVDA", decision="PASS"),
            _review_row(ticker="AMD", decision="WATCH", analysis_rank="5"),
        ],
    )
    _write_csv(
        root
        / "opportunities"
        / "archive"
        / "2026-06-08_pre_market_133641Z_opportunities.csv",
        [_opportunity_row(ticker="NVDA")],
    )
    _write_csv(
        root
        / "backtests"
        / "archive"
        / "2026-06-08_pre_market_133641Z_review_outcomes.csv",
        [_outcome_row(ticker="AMD", exit_type="NO_ENTRY", entry_triggered="false")],
    )
    _write_csv(
        root
        / "backtests"
        / "archive"
        / "2026-06-08_pre_market_133641Z_opportunities_outcomes.csv",
        [_outcome_row(ticker="NVDA")],
    )


def test_empty_archive_folders_write_empty_outputs(tmp_path: Path):
    out_dir = tmp_path / "research"
    summary, outputs = run_build_research_dataset(
        input_root=tmp_path,
        output_dir=out_dir,
    )
    assert summary.review_written == 0
    assert summary.signal_written == 0
    assert len(summary.warnings) == 3
    for name in (
        "review_master.csv",
        "opportunity_master.csv",
        "outcomes_master.csv",
        "signal_master.csv",
    ):
        path = out_dir / name
        assert path.is_file()
        with path.open(encoding="utf-8") as f:
            reader = csv.DictReader(f)
            assert reader.fieldnames
            assert list(reader) == []
    assert outputs["signal_master"] == path


def test_review_archive_consolidation(tmp_path: Path):
    _fixture_tree(tmp_path)
    summary, outputs = run_build_research_dataset(
        input_root=tmp_path,
        output_dir=tmp_path / "research",
    )
    assert summary.review_loaded == 2
    with outputs["review_master"].open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 2
    assert rows[0]["source_type"] == "review"
    assert rows[0]["source_file"].endswith("_review.csv")
    assert "run_id" in rows[0]


def test_opportunity_archive_consolidation(tmp_path: Path):
    _fixture_tree(tmp_path)
    _, outputs = run_build_research_dataset(
        input_root=tmp_path,
        output_dir=tmp_path / "research",
    )
    with outputs["opportunity_master"].open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    assert rows[0]["source_type"] == "opportunity"
    assert rows[0]["ticker"] == "NVDA"


def test_outcome_archive_source_type_classification(tmp_path: Path):
    _fixture_tree(tmp_path)
    _, outputs = run_build_research_dataset(
        input_root=tmp_path,
        output_dir=tmp_path / "research",
    )
    with outputs["outcomes_master"].open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    types = {row["source_type"] for row in rows}
    assert types == {"review_outcome", "opportunity_outcome"}


def test_outcome_source_type_from_filename():
    assert (
        outcome_source_type_from_filename("2026-06-08_pre_market_133641Z_review_outcomes.csv")
        == "review_outcome"
    )
    assert (
        outcome_source_type_from_filename(
            "2026-06-08_pre_market_133641Z_opportunities_outcomes.csv"
        )
        == "opportunity_outcome"
    )
    assert outcome_source_type_from_filename("fixture_outcomes.csv") == "outcome"


def test_signal_master_prefers_opportunity_over_review(tmp_path: Path):
    review_rows = [
        {
            "run_id": "2026-06-08_pre_market_133641Z",
            "ticker": "NVDA",
            "decision": "PASS",
            "source_file": "review.csv",
        }
    ]
    opportunity_rows = [
        {
            "run_id": "2026-06-08_pre_market_133641Z",
            "ticker": "NVDA",
            "decision": "WATCH",
            "source_file": "opp.csv",
        }
    ]
    signals = build_signal_master(review_rows, opportunity_rows, [])
    assert len(signals) == 1
    assert signals[0]["source_type"] == "opportunity"
    assert signals[0]["decision"] == "WATCH"


def test_signal_master_includes_review_only_rows():
    review_rows = [
        {
            "run_id": "2026-06-08_pre_market_133641Z",
            "ticker": "AMD",
            "decision": "PASS",
            "source_file": "review.csv",
        }
    ]
    signals = build_signal_master(review_rows, [], [])
    assert len(signals) == 1
    assert signals[0]["source_type"] == "review"
    assert signals[0]["ticker"] == "AMD"


def test_signal_master_joins_outcome_fields():
    review_rows = [
        {
            "run_id": "2026-06-08_pre_market_133641Z",
            "ticker": "AMD",
            "decision": "WATCH",
            "source_file": "review.csv",
        }
    ]
    outcome_rows = [
        {
            "run_id": "2026-06-08_pre_market_133641Z",
            "ticker": "AMD",
            "source_type": "review_outcome",
            "entry_triggered": "true",
            "exit_type": "WIN",
            "r_multiple": "2.0",
            "max_favourable_excursion": "5",
            "max_adverse_excursion": "1",
            "outcome_note": "ok",
        }
    ]
    signals = build_signal_master(review_rows, [], outcome_rows)
    row = signals[0]
    assert row["has_outcome"] == "true"
    assert row["exit_type"] == "WIN"
    assert row["r_multiple"] == "2.0"
    assert row["mfe"] == "5"
    assert row["note"] == "ok"


def test_signal_master_prefers_matching_outcome_source_type():
    opportunity_rows = [
        {
            "run_id": "2026-06-08_pre_market_133641Z",
            "ticker": "NVDA",
            "decision": "WATCH",
            "source_file": "opp.csv",
        }
    ]
    outcome_rows = [
        {
            "run_id": "2026-06-08_pre_market_133641Z",
            "ticker": "NVDA",
            "source_type": "review_outcome",
            "exit_type": "LOSS",
            "entry_triggered": "true",
        },
        {
            "run_id": "2026-06-08_pre_market_133641Z",
            "ticker": "NVDA",
            "source_type": "opportunity_outcome",
            "exit_type": "WIN",
            "entry_triggered": "true",
        },
    ]
    signals = build_signal_master([], opportunity_rows, outcome_rows)
    assert signals[0]["exit_type"] == "WIN"


def test_signal_master_computes_booleans():
    row = {
        "run_id": "2026-06-08_pre_market_133641Z",
        "ticker": "NVDA",
        "review_level": "cio_reviewed",
        "decision": "BUY",
        "direction": "Long",
        "math_valid": "true",
        "stop_loss": "95",
        "target": "110",
        "planned_entry_price": "98",
        "risk_reward": "",
        "model_risk_reward": "2.6",
        "conditional_buy_limit": "true",
        "source_file": "opp.csv",
    }
    signals = build_signal_master([], [row], [])
    out = signals[0]
    assert out["is_cio_reviewed"] == "true"
    assert out["is_rank_excluded"] == "false"
    assert out["is_buy"] == "true"
    assert out["is_conditional"] == "true"
    assert out["is_actionable_candidate"] == "true"
    assert out["resolved_planned_entry"] == "98"
    assert out["resolved_rr"] == "2.6"


def test_dedupe_removes_archive_and_latest_duplicates(tmp_path: Path):
    row = _review_row()
    archive = tmp_path / "reviews" / "archive" / "2026-06-08_pre_market_133641Z_review.csv"
    latest = tmp_path / "reviews" / "2026-06-08_pre_market_review.csv"
    _write_csv(archive, [row])
    _write_csv(latest, [row])
    summary, _outputs = run_build_research_dataset(
        input_root=tmp_path,
        output_dir=tmp_path / "research",
        include_latest=True,
        dedupe=True,
    )
    assert summary.review_loaded == 2
    assert summary.review_written == 1

    merged = dedupe_master_rows(
        [
            {"run_id": row["run_id"], "ticker": row["ticker"], "source_type": "review", "source_file": "a.csv"},
            {"run_id": row["run_id"], "ticker": row["ticker"], "source_type": "review", "source_file": "b.csv"},
        ]
    )
    assert len(merged) == 1


def test_cli_help_includes_build_research_dataset():
    try:
        main(["build-research-dataset", "--help"])
    except SystemExit as exc:
        assert exc.code == 0


def test_cli_writes_all_four_output_files(tmp_path: Path):
    _fixture_tree(tmp_path)
    out_dir = tmp_path / "research"
    code = main(
        [
            "build-research-dataset",
            "--input-root",
            str(tmp_path),
            "--output-dir",
            str(out_dir),
        ]
    )
    assert code == 0
    for name in (
        "review_master.csv",
        "opportunity_master.csv",
        "outcomes_master.csv",
        "signal_master.csv",
    ):
        assert (out_dir / name).is_file()


if __name__ == "__main__":
    tests = [
        test_empty_archive_folders_write_empty_outputs,
        test_review_archive_consolidation,
        test_opportunity_archive_consolidation,
        test_outcome_archive_source_type_classification,
        test_outcome_source_type_from_filename,
        test_signal_master_prefers_opportunity_over_review,
        test_signal_master_includes_review_only_rows,
        test_signal_master_joins_outcome_fields,
        test_signal_master_prefers_matching_outcome_source_type,
        test_signal_master_computes_booleans,
        test_dedupe_removes_archive_and_latest_duplicates,
        test_cli_help_includes_build_research_dataset,
        test_cli_writes_all_four_output_files,
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
