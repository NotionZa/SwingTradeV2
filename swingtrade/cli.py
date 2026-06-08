from __future__ import annotations

import argparse
import logging
import sys

from pathlib import Path

from swingtrade.candidate_review import export_candidate_review_csv
from swingtrade.opportunity_export import export_opportunity_csv
from swingtrade.outcome_tracker import format_summary_text, run_outcome_backtest
from swingtrade.backtest_summary import (
    format_summary_console,
    filters_from_namespace,
    run_backtest_summary,
)
from swingtrade.discord_bot import run_bot
from swingtrade.logging_config import configure_logging
from swingtrade.model_guard import check_models
from swingtrade.pipeline import (
    SingleAgentName,
    run_pipeline,
    run_single_agent,
)
from swingtrade.settings import get_settings
from swingtrade.universe_pools import format_universe_status

logger = logging.getLogger(__name__)

# Normalized CLI token (lowercase, underscores -> hyphens) -> pipeline agent id
_CLI_AGENT_ALIASES: dict[str, SingleAgentName] = {
    "market-sentiment": "market_sentiment",
    "market_sentiment": "market_sentiment",
    "hard-veto": "hard_veto",
    "hard_veto": "hard_veto",
    "technical": "technical_analysis",
    "technical-analysis": "technical_analysis",
    "technical_analysis": "technical_analysis",
    "sentiment": "sentiment",
    "cio": "cio",
}


def _parse_run_agent_name(raw: str) -> SingleAgentName:
    key = raw.strip().lower().replace("_", "-")
    try:
        return _CLI_AGENT_ALIASES[key]
    except KeyError:
        hint = "market-sentiment, hard-veto, technical, sentiment, cio (case-insensitive)"
        raise argparse.ArgumentTypeError(f"unknown agent {raw!r}; expected {hint}") from None


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    configure_logging()
    parser = argparse.ArgumentParser(prog="swingtrade")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="Run scheduled agent pipeline once")
    p_run.add_argument(
        "--session",
        choices=("pre_market", "post_market"),
        required=True,
    )
    p_run.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not POST to Discord webhooks (still calls LLMs unless key missing)",
    )
    p_run.add_argument(
        "--max-tickers",
        type=int,
        default=None,
        help="Cap trade-candidate tickers per run (default: full core watchlist size)",
    )
    p_run.add_argument(
        "--max-analysis-tickers",
        type=int,
        default=None,
        help="Post-veto survivors sent to Technical/Sentiment (default 30)",
    )
    p_run.add_argument(
        "--max-cio-tickers",
        type=int,
        default=None,
        help="Top-ranked symbols sent to CIO after TA/Sentiment (default 12)",
    )
    p_run.add_argument(
        "--max-downstream-tickers",
        type=int,
        default=None,
        help="Legacy: sets both analysis and CIO caps when the new flags are omitted",
    )
    p_run.add_argument(
        "--analysis-batch-size",
        type=int,
        default=15,
        help="Max symbols per Technical/Sentiment LLM call when pool exceeds this (default 15)",
    )

    p_agent = sub.add_parser(
        "run-agent",
        help="Run a single agent; still posts that agent's Discord webhook(s). "
        "May run upstream agents silently (no Discord) when needed for inputs.",
    )
    p_agent.add_argument(
        "agent",
        type=_parse_run_agent_name,
        metavar="AGENT",
        help="e.g. Technical or technical (market-sentiment, hard-veto, sentiment, cio; case-insensitive)",
    )
    p_agent.add_argument(
        "--session",
        choices=("pre_market", "post_market"),
        required=True,
    )
    p_agent.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not POST to Discord webhooks (LLMs still run when API key is set)",
    )
    p_agent.add_argument(
        "--max-tickers",
        type=int,
        default=None,
        help="Cap trade-candidate tickers (default: full core watchlist size)",
    )
    p_agent.add_argument(
        "--max-analysis-tickers",
        type=int,
        default=None,
        help="Post-veto survivors sent to Technical/Sentiment (default 30)",
    )
    p_agent.add_argument(
        "--max-cio-tickers",
        type=int,
        default=None,
        help="Top-ranked symbols sent to CIO (default 12)",
    )
    p_agent.add_argument(
        "--max-downstream-tickers",
        type=int,
        default=None,
        help="Legacy: sets both analysis and CIO caps when the new flags are omitted",
    )
    p_agent.add_argument(
        "--analysis-batch-size",
        type=int,
        default=15,
        help="Max symbols per Technical/Sentiment LLM call when pool exceeds this (default 15)",
    )

    sub.add_parser("bot", help="Run Discord watchlist slash-command bot")
    p_models = sub.add_parser(
        "check-models",
        help="Validate configured Anthropic model IDs against policy/cache",
    )
    p_models.add_argument(
        "--refresh",
        action="store_true",
        help="Refresh local Anthropic model cache from provider models-list endpoint",
    )

    p_review = sub.add_parser(
        "review-candidates",
        help="Export a candidate JSONL file to a CSV review summary under data/reviews/",
    )
    p_review.add_argument(
        "--file",
        type=Path,
        required=True,
        help="Path to candidate JSONL, e.g. data/candidates/2026-05-21_pre_market.jsonl",
    )
    p_review.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional CSV output path (default: data/reviews/<stem>_review.csv)",
    )
    p_review.add_argument(
        "--all-runs",
        action="store_true",
        help="Export every JSONL row (all run_timestamp_utc values); default is latest run only",
    )

    p_opp = sub.add_parser(
        "export-opportunities",
        help="Export tactical opportunity watchlist CSV under data/opportunities/",
    )
    p_opp.add_argument(
        "--file",
        type=Path,
        required=True,
        help="Path to candidate JSONL, e.g. data/candidates/2026-06-03_post_market.jsonl",
    )
    p_opp.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional CSV output path (default: data/opportunities/<stem>_opportunities.csv)",
    )
    p_opp.add_argument(
        "--all-runs",
        action="store_true",
        help="Export from every JSONL row; default is latest run only",
    )
    p_opp.add_argument(
        "--include-no-zone",
        action="store_true",
        help="Include NO_ACTIONABLE_ZONE rows (excluded by default)",
    )

    p_backtest = sub.add_parser(
        "backtest-outcomes",
        help="Replay historical outcomes for opportunity/review CSV signals",
    )
    p_backtest.add_argument(
        "--file",
        type=Path,
        required=True,
        help="Path to opportunity or review CSV, e.g. data/opportunities/2026-06-05_pre_market_opportunities.csv",
    )
    p_backtest.add_argument(
        "--days",
        type=int,
        default=10,
        help="Trading-day horizon after signal date (default 10)",
    )
    p_backtest.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional output CSV path (default: data/backtests/<stem>_outcomes.csv)",
    )

    p_summary = sub.add_parser(
        "summarize-backtests",
        help="Aggregate historical outcome CSVs under data/backtests/",
    )
    p_summary.add_argument(
        "--input",
        type=Path,
        default=None,
        help="Outcome CSV file or directory (default: data/backtests)",
    )
    p_summary.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Overall summary CSV (default: data/backtests/summary.csv)",
    )
    p_summary.add_argument(
        "--by-run-output",
        type=Path,
        default=None,
        help="By-run summary CSV (default: data/backtests/summary_by_run.csv)",
    )
    p_summary.add_argument(
        "--since",
        default=None,
        help="Include signals on/after YYYY-MM-DD",
    )
    p_summary.add_argument(
        "--until",
        default=None,
        help="Include signals on/before YYYY-MM-DD",
    )
    p_summary.add_argument(
        "--session",
        default=None,
        help="Filter by session (e.g. pre_market, post_market)",
    )
    p_summary.add_argument(
        "--decision",
        default=None,
        help="Filter by decision (BUY, WATCH, PASS, SCREENED)",
    )
    p_summary.add_argument(
        "--review-level",
        dest="review_level",
        default=None,
        help="Filter by review_level (cio_reviewed, rank_excluded, ...)",
    )
    p_summary.add_argument(
        "--opportunity-status",
        dest="opportunity_status",
        default=None,
        help="Filter by opportunity_status (PULLBACK_REQUIRED, NEAR_BUY, ...)",
    )
    p_summary.add_argument(
        "--strategy",
        default=None,
        help="Filter by strategy (Momentum, Pullback, Breakout, ...)",
    )
    p_summary.add_argument(
        "--only-valid-geometry",
        action="store_true",
        help="Keep rows with resolved/planned entry, stop, and target",
    )
    p_summary.add_argument(
        "--only-planned",
        action="store_true",
        help="Keep rows with planned_entry_price",
    )
    p_summary.add_argument(
        "--only-conditional",
        action="store_true",
        help="Keep rows with conditional_buy_limit=true",
    )
    p_summary.add_argument(
        "--only-closed",
        action="store_true",
        help="Keep WIN/LOSS outcomes only",
    )
    p_summary.add_argument(
        "--exclude-legacy",
        action="store_true",
        help="Drop rows lacking run_id and planned/opportunity tracking",
    )
    p_summary.add_argument(
        "--include-archive",
        action="store_true",
        help="Also load data/backtests/archive/*_outcomes.csv (deduped by run_id)",
    )

    p_univ = sub.add_parser(
        "universe-status",
        help="Print universe pool sizes, sources, and truncation diagnostics",
    )
    p_univ.add_argument(
        "--max-tickers",
        type=int,
        default=None,
        help="Trade-pool cap to simulate (default: full core watchlist size)",
    )
    p_univ.add_argument(
        "--max-analysis-tickers",
        type=int,
        default=None,
        help="Post-veto TA/Sentiment cap (default 30)",
    )
    p_univ.add_argument(
        "--max-cio-tickers",
        type=int,
        default=None,
        help="CIO review cap (default 12)",
    )

    args = parser.parse_args(argv)

    if args.command == "run":
        get_settings.cache_clear()  # type: ignore[attr-defined]
        run_pipeline(
            session=args.session,  # type: ignore[arg-type]
            dry_run=args.dry_run,
            max_tickers=args.max_tickers,
            max_analysis_tickers=args.max_analysis_tickers,
            max_cio_tickers=args.max_cio_tickers,
            max_downstream_tickers=args.max_downstream_tickers,
            analysis_batch_size=args.analysis_batch_size,
        )
        return 0
    if args.command == "run-agent":
        get_settings.cache_clear()  # type: ignore[attr-defined]
        run_single_agent(
            agent=args.agent,
            session=args.session,  # type: ignore[arg-type]
            dry_run=args.dry_run,
            max_tickers=args.max_tickers,
            max_analysis_tickers=args.max_analysis_tickers,
            max_cio_tickers=args.max_cio_tickers,
            max_downstream_tickers=args.max_downstream_tickers,
            analysis_batch_size=args.analysis_batch_size,
        )
        return 0
    if args.command == "bot":
        get_settings.cache_clear()  # type: ignore[attr-defined]
        run_bot()
        return 0
    if args.command == "review-candidates":
        try:
            out = export_candidate_review_csv(
                args.file,
                output_path=args.output,
                all_runs=bool(args.all_runs),
            )
        except (FileNotFoundError, ValueError) as e:
            logger.error("%s", e)
            return 1
        print(out)
        return 0
    if args.command == "export-opportunities":
        try:
            out = export_opportunity_csv(
                args.file,
                output_path=args.output,
                all_runs=bool(args.all_runs),
                include_no_zone=bool(args.include_no_zone),
            )
        except (FileNotFoundError, ValueError) as e:
            logger.error("%s", e)
            return 1
        print(out)
        return 0
    if args.command == "backtest-outcomes":
        try:
            out_path, _rows, summary, extras = run_outcome_backtest(
                args.file,
                horizon_days=int(args.days),
                output_path=args.output,
            )
        except (FileNotFoundError, ValueError) as e:
            logger.error("%s", e)
            return 1
        print(out_path)
        print(format_summary_text(summary, extras))
        return 0
    if args.command == "summarize-backtests":
        summary_filters = filters_from_namespace(args)
        try:
            out_summary, out_by_run, overall, by_run, raw_loaded, filtered = (
                run_backtest_summary(
                    args.input,
                    output_path=args.output,
                    by_run_output_path=args.by_run_output,
                    filters=summary_filters,
                    include_archive=getattr(args, "include_archive", False),
                )
            )
        except (FileNotFoundError, ValueError) as e:
            logger.error("%s", e)
            return 1
        print(out_summary)
        print(out_by_run)
        print(
            format_summary_console(
                overall,
                by_run,
                raw_loaded=raw_loaded,
                filtered_rows=filtered,
                filters=summary_filters,
            )
        )
        return 0
    if args.command == "universe-status":
        get_settings.cache_clear()  # type: ignore[attr-defined]
        settings = get_settings()
        print(
            format_universe_status(
                settings,
                max_tickers=args.max_tickers,
                max_analysis_tickers=args.max_analysis_tickers,
                max_cio_tickers=args.max_cio_tickers,
            )
        )
        return 0
    if args.command == "check-models":
        get_settings.cache_clear()  # type: ignore[attr-defined]
        settings = get_settings()
        try:
            result = check_models(settings, refresh=bool(args.refresh))
        except ValueError as e:
            logger.error("%s", e)
            return 1
        print("Anthropic model validation: OK")
        for entry in sorted(result.entries, key=lambda x: x.family):
            print(f"- {entry.family}: {entry.model_id} ({entry.status})")
        if result.cache_path:
            age = (
                f"{result.cache_age_days:.1f}d"
                if isinstance(result.cache_age_days, (int, float))
                else "unknown"
            )
            print(f"- cache: {result.cache_path} (age={age})")
        for w in result.warnings:
            print(f"- warning: {w}")
        return 0
    return 2
