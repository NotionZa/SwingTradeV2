from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from swingtrade.backtest_summary import default_backtests_dir, run_backtest_summary
from swingtrade.candidate_review import (
    export_candidate_review_csv,
    review_csv_path_for_jsonl,
)
from swingtrade.opportunity_export import (
    export_opportunity_csv,
    opportunity_csv_path_for_jsonl,
)
from swingtrade.outcome_tracker import backtest_csv_path_for_input, run_outcome_backtest
from swingtrade.research_dataset import (
    default_research_dir,
    run_build_research_dataset,
)
from swingtrade.research_diagnostics import (
    default_edge_diagnostics_csv,
    default_edge_diagnostics_md,
    run_research_edge_diagnostics,
)

logger = logging.getLogger(__name__)

ExportReviewFn = Callable[..., Path]
ExportOpportunityFn = Callable[..., Path]
BacktestFn = Callable[..., tuple]
SummarizeFn = Callable[..., tuple]
BuildDatasetFn = Callable[..., tuple]
DiagnoseFn = Callable[..., tuple]


@dataclass(frozen=True)
class ResearchRefreshPaths:
    candidate_file: Path
    review_csv: Path
    opportunity_csv: Path
    review_outcomes_csv: Path
    opportunity_outcomes_csv: Path
    summary_csv: Path
    summary_by_run_csv: Path
    review_master: Path
    opportunity_master: Path
    outcomes_master: Path
    signal_master: Path
    edge_diagnostics_csv: Path
    edge_diagnostics_md: Path


@dataclass
class ResearchRefreshResult:
    paths: ResearchRefreshPaths
    steps_completed: list[str] = field(default_factory=list)
    dry_run: bool = False


class ResearchRefreshError(Exception):
    """Raised when a workflow step fails."""

    def __init__(self, step: str, cause: Exception) -> None:
        self.step = step
        self.cause = cause
        super().__init__(f"Research refresh failed at step '{step}': {cause}")


def derive_refresh_paths(candidate_file: Path) -> ResearchRefreshPaths:
    """Map candidate JSONL to expected review/opportunity/backtest/research outputs."""
    candidate = candidate_file.resolve()
    review_csv = review_csv_path_for_jsonl(candidate)
    opportunity_csv = opportunity_csv_path_for_jsonl(candidate)
    review_outcomes = backtest_csv_path_for_input(review_csv)
    opportunity_outcomes = backtest_csv_path_for_input(opportunity_csv)
    backtests_dir = default_backtests_dir()
    research_dir = default_research_dir()
    return ResearchRefreshPaths(
        candidate_file=candidate,
        review_csv=review_csv,
        opportunity_csv=opportunity_csv,
        review_outcomes_csv=review_outcomes,
        opportunity_outcomes_csv=opportunity_outcomes,
        summary_csv=backtests_dir / "summary.csv",
        summary_by_run_csv=backtests_dir / "summary_by_run.csv",
        review_master=research_dir / "review_master.csv",
        opportunity_master=research_dir / "opportunity_master.csv",
        outcomes_master=research_dir / "outcomes_master.csv",
        signal_master=research_dir / "signal_master.csv",
        edge_diagnostics_csv=default_edge_diagnostics_csv(),
        edge_diagnostics_md=default_edge_diagnostics_md(),
    )


def plan_research_refresh_steps(
    *,
    skip_research_dataset: bool = False,
    skip_diagnostics: bool = False,
) -> list[str]:
    steps = [
        "review-candidates",
        "export-opportunities",
        "backtest-outcomes (review)",
        "backtest-outcomes (opportunities)",
        "summarize-backtests",
    ]
    if not skip_research_dataset:
        steps.append("build-research-dataset")
    if not skip_diagnostics:
        steps.append("diagnose-research-edge")
    return steps


def format_research_refresh_plan(
    paths: ResearchRefreshPaths,
    *,
    days: int,
    include_archive: bool,
    skip_research_dataset: bool,
    skip_diagnostics: bool,
) -> str:
    lines = [
        "**Research refresh plan (dry-run)**",
        f"- candidate_file: {paths.candidate_file}",
        f"- horizon_days: {days}",
        f"- include_archive: {include_archive}",
        f"- skip_research_dataset: {skip_research_dataset}",
        f"- skip_diagnostics: {skip_diagnostics}",
        "",
        "**Steps**",
    ]
    for step in plan_research_refresh_steps(
        skip_research_dataset=skip_research_dataset,
        skip_diagnostics=skip_diagnostics,
    ):
        lines.append(f"- {step}")
    lines.extend(
        [
            "",
            "**Expected outputs**",
            f"- review_csv: {paths.review_csv}",
            f"- opportunity_csv: {paths.opportunity_csv}",
            f"- review_outcomes_csv: {paths.review_outcomes_csv}",
            f"- opportunity_outcomes_csv: {paths.opportunity_outcomes_csv}",
            f"- summary_csv: {paths.summary_csv}",
            f"- summary_by_run_csv: {paths.summary_by_run_csv}",
            f"- review_master: {paths.review_master}",
            f"- opportunity_master: {paths.opportunity_master}",
            f"- outcomes_master: {paths.outcomes_master}",
            f"- signal_master: {paths.signal_master}",
            f"- edge_diagnostics_csv: {paths.edge_diagnostics_csv}",
            f"- edge_diagnostics_md: {paths.edge_diagnostics_md}",
        ]
    )
    return "\n".join(lines)


def format_research_refresh_summary(result: ResearchRefreshResult) -> str:
    paths = result.paths
    lines = [
        "**Research refresh complete**",
        f"- candidate_file: {paths.candidate_file}",
        f"- steps_completed: {len(result.steps_completed)}",
    ]
    for step in result.steps_completed:
        lines.append(f"  - {step}")
    lines.extend(
        [
            "",
            "**Outputs**",
            f"- review_csv: {paths.review_csv}",
            f"- opportunity_csv: {paths.opportunity_csv}",
            f"- review_outcomes_csv: {paths.review_outcomes_csv}",
            f"- opportunity_outcomes_csv: {paths.opportunity_outcomes_csv}",
            f"- summary_csv: {paths.summary_csv}",
            f"- summary_by_run_csv: {paths.summary_by_run_csv}",
            f"- review_master: {paths.review_master}",
            f"- opportunity_master: {paths.opportunity_master}",
            f"- outcomes_master: {paths.outcomes_master}",
            f"- signal_master: {paths.signal_master}",
            f"- edge_diagnostics_csv: {paths.edge_diagnostics_csv}",
            f"- edge_diagnostics_md: {paths.edge_diagnostics_md}",
        ]
    )
    return "\n".join(lines)


def _run_step(step: str, fn: Callable[[], None]) -> None:
    try:
        fn()
    except Exception as exc:
        raise ResearchRefreshError(step, exc) from exc


def run_research_refresh(
    candidate_file: Path,
    *,
    days: int = 10,
    include_archive: bool = False,
    skip_diagnostics: bool = False,
    skip_research_dataset: bool = False,
    dry_run: bool = False,
    export_review: ExportReviewFn | None = None,
    export_opportunity: ExportOpportunityFn | None = None,
    backtest: BacktestFn | None = None,
    summarize: SummarizeFn | None = None,
    build_dataset: BuildDatasetFn | None = None,
    diagnose: DiagnoseFn | None = None,
) -> ResearchRefreshResult:
    """Run the full post-pipeline research refresh workflow."""
    if days < 1:
        raise ValueError("days must be >= 1")

    candidate_file = candidate_file.resolve()
    if not dry_run and not candidate_file.is_file():
        raise FileNotFoundError(f"Candidate JSONL not found: {candidate_file}")

    paths = derive_refresh_paths(candidate_file)
    result = ResearchRefreshResult(paths=paths, dry_run=dry_run)

    if dry_run:
        return result

    review_exporter = export_review or export_candidate_review_csv
    opportunity_exporter = export_opportunity or export_opportunity_csv
    backtest_runner = backtest or run_outcome_backtest
    summarize_runner = summarize or run_backtest_summary
    dataset_builder = build_dataset or run_build_research_dataset
    diagnostics_runner = diagnose or run_research_edge_diagnostics

    def _review() -> None:
        review_exporter(candidate_file, output_path=paths.review_csv)

    def _opportunity() -> None:
        opportunity_exporter(candidate_file, output_path=paths.opportunity_csv)

    def _backtest_review() -> None:
        backtest_runner(
            paths.review_csv,
            horizon_days=days,
            output_path=paths.review_outcomes_csv,
        )

    def _backtest_opportunity() -> None:
        backtest_runner(
            paths.opportunity_csv,
            horizon_days=days,
            output_path=paths.opportunity_outcomes_csv,
        )

    def _summarize() -> None:
        summarize_runner(
            default_backtests_dir(),
            output_path=paths.summary_csv,
            by_run_output_path=paths.summary_by_run_csv,
            include_archive=include_archive,
        )

    def _build_dataset() -> None:
        dataset_builder()

    def _diagnose() -> None:
        diagnostics_runner()

    _run_step("review-candidates", _review)
    result.steps_completed.append("review-candidates")

    _run_step("export-opportunities", _opportunity)
    result.steps_completed.append("export-opportunities")

    _run_step("backtest-outcomes (review)", _backtest_review)
    result.steps_completed.append("backtest-outcomes (review)")

    _run_step("backtest-outcomes (opportunities)", _backtest_opportunity)
    result.steps_completed.append("backtest-outcomes (opportunities)")

    _run_step("summarize-backtests", _summarize)
    result.steps_completed.append("summarize-backtests")

    if not skip_research_dataset:
        _run_step("build-research-dataset", _build_dataset)
        result.steps_completed.append("build-research-dataset")

    if not skip_diagnostics:
        _run_step("diagnose-research-edge", _diagnose)
        result.steps_completed.append("diagnose-research-edge")

    logger.info(
        "Research refresh completed for %s (%s steps)",
        candidate_file,
        len(result.steps_completed),
    )
    return result
