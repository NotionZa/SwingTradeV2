from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Literal

from swingtrade.cio_packet import (
    build_cio_packet,
    diagnose_cio_packet,
    format_cio_user_message,
    packet_section_char_counts,
)

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
    analysis_tickers: list[str] = field(default_factory=list)
    prior_structured: dict[str, dict[str, Any]] = field(default_factory=dict)

    def add(self, agent_id: str, result: AgentResult) -> None:
        """Store a deep copy of *structured* only (never ``discord_markdown``)."""
        self.prior_structured[agent_id] = deepcopy(result.structured)

    def build_cio_packet(
        self,
        session: SessionName,
        *,
        cio_symbols: list[str] | None = None,
    ) -> dict[str, Any]:
        cio = list(cio_symbols) if cio_symbols is not None else list(self.tickers)
        analysis = list(self.analysis_tickers) if self.analysis_tickers else cio
        return build_cio_packet(
            self.prior_structured,
            session,
            cio_symbols=cio,
            analysis_symbols=analysis,
        )

    def cio_packet_section_char_counts(
        self, packet: dict[str, Any]
    ) -> dict[str, int]:
        return packet_section_char_counts(packet)

    def cio_packet_diagnostics(
        self,
        session: SessionName,
        *,
        cio_symbols: list[str] | None = None,
    ) -> dict[str, Any]:
        cio = list(cio_symbols) if cio_symbols is not None else list(self.tickers)
        analysis = list(self.analysis_tickers) if self.analysis_tickers else cio
        return diagnose_cio_packet(
            self.prior_structured,
            session,
            cio_symbols=cio,
            analysis_symbols=analysis,
        )

    def prior_structured_json(self) -> str:
        """Full prior agent structured blobs (for debugging only; not sent to CIO)."""
        return json.dumps(
            self.prior_structured,
            indent=2,
            ensure_ascii=False,
            default=str,
        )

    def cio_user_message(
        self,
        session: SessionName,
        *,
        cio_symbols: list[str] | None = None,
    ) -> str:
        """Exact user payload sent to CIO (compact decision packet only)."""
        packet = self.build_cio_packet(session, cio_symbols=cio_symbols)
        return format_cio_user_message(packet)
