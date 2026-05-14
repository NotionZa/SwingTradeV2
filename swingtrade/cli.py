from __future__ import annotations

import argparse
import logging
import sys

from swingtrade.discord_bot import run_bot
from swingtrade.logging_config import configure_logging
from swingtrade.pipeline import run_pipeline
from swingtrade.settings import get_settings

logger = logging.getLogger(__name__)


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

    sub.add_parser("bot", help="Run Discord watchlist slash-command bot")

    args = parser.parse_args(argv)

    if args.command == "run":
        get_settings.cache_clear()  # type: ignore[attr-defined]
        run_pipeline(
            session=args.session,  # type: ignore[arg-type]
            dry_run=args.dry_run,
            max_tickers=args.max_tickers,
        )
        return 0
    if args.command == "bot":
        get_settings.cache_clear()  # type: ignore[attr-defined]
        run_bot()
        return 0
    return 2
