from __future__ import annotations

import logging
from typing import Any

from anthropic import Anthropic

from swingtrade.integrations.anthropic_client import complete_json_agent
from swingtrade.models.agents import AgentResult, RunContext, PipelineState
from swingtrade.prompt_loader import load_system_prompt
from swingtrade.settings import Settings

logger = logging.getLogger(__name__)


def run_cio(
    settings: Settings,
    ctx: RunContext,
    state: PipelineState,
    client: Anthropic,
) -> AgentResult:
    user = state.cio_user_message(ctx.session)
    logger.debug(
        "CIO user payload: %s agent structured blobs, %s chars",
        len(state.prior_structured),
        len(user),
    )
    raw = complete_json_agent(
        client,
        model=settings.anthropic_model_opus,
        system=load_system_prompt("cio"),
        user=user,
        max_tokens=8192,
    )
    md = str(raw.get("discord_markdown", "")).strip() or "_No CIO output_"
    structured = raw.get("structured")
    if not isinstance(structured, dict):
        structured = {}
    return AgentResult(
        agent_id="cio",
        discord_markdown=md,
        structured=structured,
        model_used=settings.anthropic_model_opus,
    )
