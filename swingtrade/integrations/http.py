"""HTTP helpers for outbound calls (timeouts, no secret logging)."""

from __future__ import annotations

import logging
import time
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

# Discord webhook `content` max length per request (plain text).
DISCORD_WEBHOOK_CONTENT_MAX = 2000


def chunk_discord_webhook_content(
    text: str, *, max_chars: int = DISCORD_WEBHOOK_CONTENT_MAX
) -> list[str]:
    """Split *text* into segments each <= *max_chars*, preferring line breaks."""
    if not text or not text.strip():
        return []
    chunks: list[str] = []
    rest = text
    while rest:
        if len(rest) <= max_chars:
            chunks.append(rest)
            break
        window = rest[:max_chars]
        nl = window.rfind("\n")
        if nl > 0:
            cut = nl + 1
        else:
            cut = max_chars
        chunks.append(rest[:cut])
        rest = rest[cut:]
    return chunks


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
    if not url.strip():
        logger.warning("Skipping empty webhook URL")
        return
    chunks = chunk_discord_webhook_content(content)
    if not chunks:
        logger.warning("Skipping webhook post: empty content for %s", redact_url(url))
        return
    if dry_run:
        logger.info(
            "DRY-RUN webhook %s parts=%s total_chars=%s",
            redact_url(url),
            len(chunks),
            len(content),
        )
        return
    n = len(chunks)
    for i, piece in enumerate(chunks):
        payload = {"content": piece}
        try:
            r = client.post(url, json=payload)
            r.raise_for_status()
        except httpx.HTTPError as e:
            logger.error(
                "Webhook post failed for %s part %s/%s: %s",
                redact_url(url),
                i + 1,
                n,
                type(e).__name__,
            )
            raise
        if i < n - 1:
            time.sleep(0.25)
