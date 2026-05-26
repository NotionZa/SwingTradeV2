from __future__ import annotations

import argparse
import logging
import sys

from pathlib import Path

from swingtrade.candidate_review import export_candidate_review_csv
from swingtrade.discord_bot import run_bot
from swingtrade.logging_config import configure_logging
from swingtrade.pipeline import (
    SingleAgentName,
    run_pipeline,
    run_single_agent,
)
from swingtrade.settings import get_settings

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
        default=30,
        help="Cap trade-candidate tickers per run (excludes Context-only proxies)",
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
        default=30,
        help="Cap trade-candidate tickers (same as full pipeline)",
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
            out = export_candidate_review_csv(args.file, output_path=args.output)
        except (FileNotFoundError, ValueError) as e:
            logger.error("%s", e)
            return 1
        print(out)
        return 0
    return 2
