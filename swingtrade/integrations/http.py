"""HTTP helpers for outbound calls (timeouts, no secret logging)."""

from __future__ import annotations

import logging
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)


def redact_url(url: str) -> str:
    try:
        p = urlparse(url)
        host = p.hostname or ""
        path = p.path or ""
        if len(path) > 24:
            path = path[:12] + "…" + path[-8:]
        return f"{p.scheme}://{host}{path}"
    except Exception:
        return "[invalid_url]"


def webhook_client(timeout: float) -> httpx.Client:
    return httpx.Client(timeout=timeout, follow_redirects=True)


def post_discord_webhook(
    client: httpx.Client,
    url: str,
    content: str,
    *,
    dry_run: bool,
) -> None:
    if dry_run:
        logger.info("DRY-RUN webhook %s chars=%s", redact_url(url), len(content))
        return
    if not url.strip():
        logger.warning("Skipping empty webhook URL")
        return
    payload = {"content": content[:2000]}
    try:
        r = client.post(url, json=payload)
        r.raise_for_status()
    except httpx.HTTPError as e:
        logger.error("Webhook post failed for %s: %s", redact_url(url), type(e).__name__)
        raise
