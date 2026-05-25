from __future__ import annotations

import json
from copy import deepcopy
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
    analysis_tickers: list[str] = field(default_factory=list)
    prior_structured: dict[str, dict[str, Any]] = field(default_factory=dict)

    def add(self, agent_id: str, result: AgentResult) -> None:
        """Store a deep copy of *structured* only (never ``discord_markdown``)."""
        self.prior_structured[agent_id] = deepcopy(result.structured)

    def prior_structured_for_cio(self, cio_symbols: list[str] | None = None) -> str:
        """Structured payloads for CIO; TA/Sentiment ticker maps trimmed to *cio_symbols*."""
        payload = deepcopy(self.prior_structured)
        if cio_symbols:
            sym_set = {s.strip().upper() for s in cio_symbols if s and str(s).strip()}
            ta = payload.get("technical_analysis")
            if isinstance(ta, dict):
                tickers = ta.get("tickers")
                if isinstance(tickers, dict):
                    ta["tickers"] = {
                        k: v
                        for k, v in tickers.items()
                        if str(k).strip().upper() in sym_set
                    }
                scores = ta.get("scores")
                if isinstance(scores, dict):
                    ta["scores"] = {
                        k: v
                        for k, v in scores.items()
                        if str(k).strip().upper() in sym_set
                    }
            se = payload.get("sentiment")
            if isinstance(se, dict):
                per = se.get("per_ticker")
                if isinstance(per, dict):
                    se["per_ticker"] = {
                        k: v
                        for k, v in per.items()
                        if str(k).strip().upper() in sym_set
                    }
        return json.dumps(
            payload,
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
        """Exact user payload sent to the CIO model (structured only, no discord_markdown)."""
        cio = list(cio_symbols) if cio_symbols is not None else list(self.tickers)
        analysis = list(self.analysis_tickers) if self.analysis_tickers else list(cio)
        return (
            f"Session={session}\n"
            f"Analysis_universe ({len(analysis)} tickers after hard veto cap): "
            f"{json.dumps(analysis, ensure_ascii=False)}\n"
            f"CIO_review_tickers ({len(cio)} tickers): "
            f"{json.dumps(cio, ensure_ascii=False)}\n"
            f"Prior structured JSON from agents (TA/Sentiment scoped to CIO_review_tickers):\n"
            f"{self.prior_structured_for_cio(cio)}"
        )
