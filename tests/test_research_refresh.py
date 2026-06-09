from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from swingtrade.cli import main
from swingtrade.research_refresh import (
    ResearchRefreshError,
    derive_refresh_paths,
    format_research_refresh_plan,
    plan_research_refresh_steps,
    run_research_refresh,
)


def test_output_path_derivation_from_candidate_file():
    candidate = Path("data/candidates/2026-06-09_pre_market.jsonl")
    paths = derive_refresh_paths(candidate)
    assert paths.review_csv.name == "2026-06-09_pre_market_review.csv"
    assert paths.opportunity_csv.name == "2026-06-09_pre_market_opportunities.csv"
    assert paths.review_outcomes_csv.name == "2026-06-09_pre_market_review_outcomes.csv"
    assert paths.opportunity_outcomes_csv.name == (
        "2026-06-09_pre_market_opportunities_outcomes.csv"
    )
    assert paths.signal_master.name == "signal_master.csv"
    assert paths.edge_diagnostics_csv.name == "edge_diagnostics.csv"


def test_dry_run_step_planning(tmp_path: Path):
    candidate = tmp_path / "2026-06-09_pre_market.jsonl"
    result = run_research_refresh(
        candidate,
        days=10,
        include_archive=True,
        dry_run=True,
    )
    assert result.dry_run is True
    assert result.steps_completed == []
    steps = plan_research_refresh_steps()
    assert steps == [
        "review-candidates",
        "export-opportunities",
        "backtest-outcomes (review)",
        "backtest-outcomes (opportunities)",
        "summarize-backtests",
        "build-research-dataset",
        "diagnose-research-edge",
    ]
    plan = format_research_refresh_plan(
        result.paths,
        days=10,
        include_archive=True,
        skip_research_dataset=False,
        skip_diagnostics=False,
    )
    assert "dry-run" in plan
    assert "review-candidates" in plan
    assert "2026-06-09_pre_market_review.csv" in plan


def test_dry_run_skips_optional_steps():
    steps = plan_research_refresh_steps(
        skip_research_dataset=True,
        skip_diagnostics=True,
    )
    assert steps == [
        "review-candidates",
        "export-opportunities",
        "backtest-outcomes (review)",
        "backtest-outcomes (opportunities)",
        "summarize-backtests",
    ]


def test_workflow_stops_on_failure(tmp_path: Path):
    candidate = tmp_path / "run.jsonl"
    candidate.write_text('{"ticker":"NVDA"}\n', encoding="utf-8")

    def boom(*_args, **_kwargs):
        raise RuntimeError("export failed")

    try:
        run_research_refresh(
            candidate,
            export_review=boom,
        )
        raise AssertionError("expected ResearchRefreshError")
    except ResearchRefreshError as exc:
        assert exc.step == "review-candidates"
        assert "export failed" in str(exc.cause)


def test_successful_workflow_with_mocked_calls(tmp_path: Path):
    candidate = tmp_path / "2026-06-09_pre_market.jsonl"
    candidate.write_text('{"ticker":"NVDA"}\n', encoding="utf-8")
    calls: list[str] = []

    def mock_review(path, *, output_path=None, **kwargs):
        calls.append("review")
        return output_path or tmp_path / "review.csv"

    def mock_opportunity(path, *, output_path=None, **kwargs):
        calls.append("opportunity")
        return output_path or tmp_path / "opp.csv"

    def mock_backtest(path, *, horizon_days=10, output_path=None, **kwargs):
        calls.append(f"backtest:{path.name}")
        return output_path or tmp_path / "out.csv", [], None, {}

    def mock_summarize(*args, **kwargs):
        calls.append("summarize")
        return tmp_path / "summary.csv", tmp_path / "by_run.csv", [], [], 0, 0

    def mock_dataset(**kwargs):
        calls.append("dataset")
        return None, {}

    def mock_diagnose(**kwargs):
        calls.append("diagnose")
        return None, []

    result = run_research_refresh(
        candidate,
        days=7,
        include_archive=True,
        export_review=mock_review,
        export_opportunity=mock_opportunity,
        backtest=mock_backtest,
        summarize=mock_summarize,
        build_dataset=mock_dataset,
        diagnose=mock_diagnose,
    )
    assert result.steps_completed == [
        "review-candidates",
        "export-opportunities",
        "backtest-outcomes (review)",
        "backtest-outcomes (opportunities)",
        "summarize-backtests",
        "build-research-dataset",
        "diagnose-research-edge",
    ]
    assert calls == [
        "review",
        "opportunity",
        "backtest:2026-06-09_pre_market_review.csv",
        "backtest:2026-06-09_pre_market_opportunities.csv",
        "summarize",
        "dataset",
        "diagnose",
    ]


def test_cli_help_includes_research_refresh():
    try:
        main(["research-refresh", "--help"])
    except SystemExit as exc:
        assert exc.code == 0


def test_cli_dry_run_wiring(tmp_path: Path):
    candidate = tmp_path / "2026-06-09_pre_market.jsonl"
    code = main(
        [
            "research-refresh",
            "--candidate-file",
            str(candidate),
            "--days",
            "10",
            "--include-archive",
            "--dry-run",
        ]
    )
    assert code == 0


if __name__ == "__main__":
    tests = [
        test_output_path_derivation_from_candidate_file,
        test_dry_run_step_planning,
        test_dry_run_skips_optional_steps,
        test_workflow_stops_on_failure,
        test_successful_workflow_with_mocked_calls,
        test_cli_help_includes_research_refresh,
        test_cli_dry_run_wiring,
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
