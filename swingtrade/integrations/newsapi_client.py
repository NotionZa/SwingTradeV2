from __future__ import annotations

import logging
from typing import Any

import httpx

from swingtrade.settings import Settings

logger = logging.getLogger(__name__)


def news_query_for_equity(symbol: str) -> str:
    """NewsAPI `q` tuned for US equity symbols (ticker-only queries often return nothing)."""
    u = symbol.strip().upper()
    return f'({u} OR "{u}" stock OR {u} earnings OR {u} shares OR {u} company)'


def _articles_from_payload(data: Any, query: str) -> tuple[list[dict[str, Any]], bool]:
    """Return (articles, is_error_payload)."""
    if not isinstance(data, dict):
        logger.warning("NewsAPI non-object JSON for query=%r", query)
        return [], True
    if data.get("status") == "error":
        logger.warning(
            "NewsAPI error for query=%r: code=%s message=%s",
            query,
            data.get("code"),
            data.get("message"),
        )
        return [], True
    return list(data.get("articles") or []), False


def _dedupe_articles(articles: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, Any]] = []
    for a in articles:
        key = (str(a.get("title") or ""), str(a.get("url") or ""))
        if key in seen or (not key[0] and not key[1]):
            continue
        seen.add(key)
        out.append(a)
        if len(out) >= limit:
            break
    return out


def _top_headlines(
    client: httpx.Client,
    *,
    api_key: str,
    query: str,
    page_size: int,
) -> list[dict[str, Any]]:
    """Fallback: /v2/top-headlines (often works when /everything is blocked on free tier)."""
    url = "https://newsapi.org/v2/top-headlines"
    r = client.get(
        url,
        params={
            "country": "us",
            "q": query,
            "pageSize": page_size,
            "apiKey": api_key,
        },
    )
    r.raise_for_status()
    data = r.json()
    articles, err = _articles_from_payload(data, query)
    if err:
        return []
    return articles


def newsapi_headlines(
    settings: Settings,
    query: str,
    *,
    page_size: int = 5,
    client: httpx.Client | None = None,
) -> list[dict[str, Any]]:
    key = settings.newsapi_key.strip()
    if not key:
        return []
    own_client = client is None
    if own_client:
        client = httpx.Client(timeout=settings.http_timeout_seconds)
    try:
        url = "https://newsapi.org/v2/everything"
        r = client.get(  # type: ignore[union-attr]
            url,
            params={
                "q": query,
                "language": "en",
                "sortBy": "publishedAt",
                "pageSize": page_size,
                "apiKey": key,
            },
        )
        r.raise_for_status()
        data = r.json()
        articles, err = _articles_from_payload(data, query)
        if err or not articles:
            if err:
                logger.info(
                    "NewsAPI /everything failed or blocked for query=%r; trying /top-headlines",
                    query,
                )
            else:
                logger.debug(
                    "NewsAPI /everything returned 0 articles for query=%r; trying /top-headlines",
                    query,
                )
            fallback = _top_headlines(
                client,  # type: ignore[arg-type]
                api_key=key,
                query=query,
                page_size=page_size,
            )
            if fallback:
                return fallback
            return articles
        return articles
    except httpx.HTTPError as e:
        body = ""
        if hasattr(e, "response") and e.response is not None:
            try:
                body = e.response.text[:400]
            except Exception:
                body = ""
        logger.warning(
            "NewsAPI HTTP error for query=%r: %s %s",
            query,
            type(e).__name__,
            body,
        )
        try:
            return _top_headlines(client, api_key=key, query=query, page_size=page_size)
        except httpx.HTTPError as e2:
            logger.warning(
                "NewsAPI top-headlines fallback failed for query=%r: %s",
                query,
                type(e2).__name__,
            )
            return []
    finally:
        if own_client and client is not None:
            client.close()


def newsapi_macro_merged(
    settings: Settings,
    client: httpx.Client,
    queries: list[str],
    *,
    per_query_size: int = 6,
    max_total: int = 20,
) -> list[dict[str, Any]]:
    """Fetch several broad macro queries and merge/dedupe for the Sentiment bundle."""
    merged: list[dict[str, Any]] = []
    for q in queries:
        merged.extend(
            newsapi_headlines(settings, q, page_size=per_query_size, client=client)
        )
    return _dedupe_articles(merged, max_total)
