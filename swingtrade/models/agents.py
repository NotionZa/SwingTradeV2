from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Literal

SessionName = Literal["pre_market", "post_market"]

# Blobs duplicated elsewhere or too large for CIO — stripped in prior_structured_for_cio().
_CIO_STRIP_TOP_LEVEL: dict[str, tuple[str, ...]] = {
    "market_sentiment": ("macro_bundle", "market_news_headlines"),
    "technical_analysis": ("inputs",),
    "sentiment": ("raw_bundle",),
}


def slim_structured_for_cio(prior: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Drop raw market/TA/news payloads; keep decision fields CIO needs."""
    out: dict[str, dict[str, Any]] = {}
    for agent_id, blob in prior.items():
        if not isinstance(blob, dict):
            continue
        strip_keys = _CIO_STRIP_TOP_LEVEL.get(agent_id, ())
        slim = {k: v for k, v in blob.items() if k not in strip_keys}
        out[agent_id] = slim
    return out


@dataclass
class RunContext:
    session: SessionName
    dry_run: bool = False


@dataclass
class AgentResult:
    """Structured output from one agent; orchestrator maps to Discord."""

    agent_id: str
    discord_markdown: str
    structured: dict[str, Any] = field(default_factory=dict)
    model_used: str = ""


@dataclass
class PipelineState:
    """Accumulated context for downstream agents (CIO sees prior JSON summaries)."""

    tickers: list[str]
    watchlist_by_category: dict[str, list[str]]
    prior_structured: dict[str, dict[str, Any]] = field(default_factory=dict)

    def add(self, agent_id: str, result: AgentResult) -> None:
        """Store a deep copy of *structured* only (never ``discord_markdown``)."""
        self.prior_structured[agent_id] = deepcopy(result.structured)

    def prior_structured_for_cio(self) -> str:
        """Structured payloads for CIO (raw TA inputs and news bundles removed)."""
        slim = slim_structured_for_cio(self.prior_structured)
        return json.dumps(slim, indent=2, ensure_ascii=False, default=str)

    def cio_user_message(self, session: SessionName) -> str:
        """Exact user payload sent to the CIO model (structured only, no discord_markdown)."""
        return (
            f"Session={session}\n"
            f"Downstream_analyzed_tickers (survivors after hard veto): "
            f"{json.dumps(self.tickers, ensure_ascii=False)}\n"
            f"Prior structured JSON from agents:\n{self.prior_structured_for_cio()}"
        )
