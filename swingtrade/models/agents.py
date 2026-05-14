from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

SessionName = Literal["pre_market", "post_market"]


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
        self.prior_structured[agent_id] = dict(result.structured)
