from __future__ import annotations

import os
from pathlib import Path

_PKG_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _PKG_DIR.parent

_SHARED_CONTRACT = "_shared_output_contract.md"


def prompts_dir() -> Path:
    override = os.environ.get("SWINGTRADE_PROMPTS_DIR", "").strip()
    if override:
        return Path(override).resolve()
    return _REPO_ROOT / "prompts"


def load_system_prompt(agent_slug: str) -> str:
    """Load the system prompt for an agent.

    Resolution order:

    1. **Split prompts** (recommended): ``prompts/<slug>_body.md`` plus
       ``prompts/<slug>_schema.md``. The loader concatenates them with a
       horizontal rule, and prepends ``prompts/_shared_output_contract.md``
       (if present) before the agent-specific schema so JSON rules stay in one
       place.

    2. **Legacy single file**: ``prompts/<slug>_system.md`` if the split pair
       is not both present.

    ``SWINGTRADE_PROMPTS_DIR`` overrides the prompts directory (see
    ``prompts_dir()``).
    """
    base = prompts_dir()
    body_path = base / f"{agent_slug}_body.md"
    schema_path = base / f"{agent_slug}_schema.md"
    legacy_path = base / f"{agent_slug}_system.md"
    shared_path = base / _SHARED_CONTRACT

    if body_path.is_file() and schema_path.is_file():
        body = body_path.read_text(encoding="utf-8").strip()
        schema_agent = schema_path.read_text(encoding="utf-8").strip()
        shared = ""
        if shared_path.is_file():
            shared = shared_path.read_text(encoding="utf-8").strip()
        schema_blocks = [b for b in (shared, schema_agent) if b]
        schema = "\n\n".join(schema_blocks)
        return f"{body}\n\n---\n\n{schema}".strip()

    if legacy_path.is_file():
        return legacy_path.read_text(encoding="utf-8").strip()

    raise FileNotFoundError(
        f"Missing system prompt for agent {agent_slug!r} under {base}: "
        f"need both {body_path.name} and {schema_path.name}, "
        f"or a legacy {legacy_path.name}."
    )
