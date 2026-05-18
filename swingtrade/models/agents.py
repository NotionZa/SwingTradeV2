from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Literal

SessionName = Literal["pre_market", "post_market"]

# Display order for CIO context Discord posts (matches pipeline order).
CIO_PRIOR_AGENT_ORDER: tuple[str, ...] = (
    "market_sentiment",
    "hard_veto",
    "technical_analysis",
    "sentiment",
)


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
        """Full structured payloads from all prior agents as JSON (no trimming)."""
        return json.dumps(
            self.prior_structured,
            indent=2,
            ensure_ascii=False,
            default=str,
        )

    def cio_user_message(self, session: SessionName) -> str:
        """Exact user payload sent to the CIO model (structured only, no discord_markdown)."""
        return (
            f"Session={session}\n"
            f"Downstream_analyzed_tickers (survivors after hard veto): "
            f"{json.dumps(self.tickers, ensure_ascii=False)}\n"
            f"Prior structured JSON from agents:\n{self.prior_structured_for_cio()}"
        )


def format_cio_context_discord(state: PipelineState, session: SessionName) -> str:
    """Human-readable mirror of ``cio_user_message`` for Discord (per-agent JSON sections)."""
    lines = [
        "# CIO agent input",
        "",
        f"**Session:** `{session}`",
        "",
        "## Survivors (scoped for TA / Sentiment / CIO)",
    ]
    if state.tickers:
        lines.append(", ".join(f"`{t}`" for t in state.tickers))
    else:
        lines.append("_none_")
    lines.extend(
        [
            "",
            "_Agent `discord_markdown` is **not** included — full `structured` JSON only._",
            "",
        ]
    )

    seen: set[str] = set()
    for agent_id in CIO_PRIOR_AGENT_ORDER:
        seen.add(agent_id)
        lines.extend(_cio_context_agent_section(agent_id, state.prior_structured.get(agent_id)))

    for agent_id in sorted(k for k in state.prior_structured if k not in seen):
        lines.extend(_cio_context_agent_section(agent_id, state.prior_structured[agent_id]))

    return "\n".join(lines).strip()


def _cio_context_agent_section(
    agent_id: str, blob: dict[str, Any] | None
) -> list[str]:
    out = [f"## {agent_id}", ""]
    if blob is None:
        out.append("_not run / no structured output_")
    else:
        out.append("```json")
        out.append(json.dumps(blob, indent=2, ensure_ascii=False, default=str))
        out.append("```")
    out.append("")
    return out
