from __future__ import annotations

import json
import re
from typing import Any


_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)


def _json_chunks_from_text(text: str) -> list[str]:
    """Candidate strings that might contain a top-level JSON object."""
    t = text.strip()
    out: list[str] = []
    for m in _FENCE_RE.finditer(t):
        inner = m.group(1).strip()
        if inner:
            out.append(inner)
    if t:
        out.append(t)
    return out


def extract_json_object(text: str) -> dict[str, Any]:
    """Parse first JSON **object** from model output (raw, fenced, or with preamble).

    Uses ``JSONDecoder.raw_decode`` so a valid object can appear after leading noise
    or inside a larger assistant message.
    """
    dec = json.JSONDecoder()
    last_err: json.JSONDecodeError | None = None
    for chunk in _json_chunks_from_text(text):
        for i, ch in enumerate(chunk):
            if ch != "{":
                continue
            try:
                obj, _end = dec.raw_decode(chunk, i)
            except json.JSONDecodeError as e:
                last_err = e
                continue
            if isinstance(obj, dict):
                return obj
    if last_err is not None:
        raise last_err
    raise json.JSONDecodeError("No JSON object found in model output", text, 0)


def sanitize_discord_markdown(md: Any) -> str:
    """Strip accidental code fences / nested JSON so Discord webhooks stay readable."""
    if md is None:
        return ""
    s = md if isinstance(md, str) else str(md)
    s = s.strip()
    if not s:
        return ""
    for _ in range(3):
        m = re.fullmatch(r"```(?:json)?\s*([\s\S]*?)\s*```", s, re.IGNORECASE | re.DOTALL)
        if not m:
            break
        inner = m.group(1).strip()
        if inner == s:
            break
        s = inner
    if s.startswith("{") and '"discord_markdown"' in s:
        try:
            obj = json.loads(s)
            if isinstance(obj, dict):
                inner_md = obj.get("discord_markdown")
                if isinstance(inner_md, str) and inner_md.strip():
                    return sanitize_discord_markdown(inner_md)
        except json.JSONDecodeError:
            pass
    return s
