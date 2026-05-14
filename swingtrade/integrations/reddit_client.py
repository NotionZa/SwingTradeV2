from __future__ import annotations

import logging
from typing import Any

import praw

from swingtrade.settings import Settings

logger = logging.getLogger(__name__)


def make_reddit_client(settings: Settings) -> praw.Reddit | None:
    if not (
        settings.reddit_client_id.strip()
        and settings.reddit_client_secret.strip()
    ):
        return None
    return praw.Reddit(
        client_id=settings.reddit_client_id,
        client_secret=settings.reddit_client_secret,
        user_agent=settings.reddit_user_agent,
    )


def reddit_search_snippets(
    reddit: praw.Reddit | None,
    query: str,
    *,
    limit: int = 5,
) -> list[dict[str, Any]]:
    if reddit is None:
        return []
    out: list[dict[str, Any]] = []
    try:
        for submission in reddit.subreddit("all").search(
            query, sort="new", time_filter="week", limit=limit
        ):
            out.append(
                {
                    "title": submission.title,
                    "subreddit": str(submission.subreddit),
                    "score": submission.score,
                    "url": submission.url,
                }
            )
    except Exception as e:
        logger.warning("Reddit search failed: %s", type(e).__name__)
    return out
