from __future__ import annotations

import json
import logging
import os
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# USD per 1M tokens (input, output). Override via ANTHROPIC_PRICING_JSON env.
DEFAULT_MODEL_PRICING: dict[str, dict[str, float]] = {
    "claude-haiku-4-5-20251001": {"input_per_million": 1.0, "output_per_million": 5.0},
    "claude-sonnet-4-6": {"input_per_million": 3.0, "output_per_million": 15.0},
    "claude-opus-4-7": {"input_per_million": 5.0, "output_per_million": 25.0},
    "claude-opus-4-1": {"input_per_million": 15.0, "output_per_million": 75.0},
    "claude-opus-4": {"input_per_million": 15.0, "output_per_million": 75.0},
}

_current_run: ContextVar["RunUsageCollector | None"] = ContextVar(
    "anthropic_usage_run", default=None
)


@dataclass
class UsageCallRecord:
    label: str
    model: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cost_usd: float | None
    timestamp: str
    pricing_known: bool = True


@dataclass
class RunUsageCollector:
    session: str
    dry_run: bool = False
    calls: list[UsageCallRecord] = field(default_factory=list)

    @property
    def total_input_tokens(self) -> int:
        return sum(c.input_tokens for c in self.calls)

    @property
    def total_output_tokens(self) -> int:
        return sum(c.output_tokens for c in self.calls)

    @property
    def total_tokens(self) -> int:
        return sum(c.total_tokens for c in self.calls)

    @property
    def total_cost_usd(self) -> float:
        return sum(c.cost_usd or 0.0 for c in self.calls)

    def add_call(
        self,
        *,
        label: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float | None,
        pricing_known: bool,
    ) -> None:
        ts = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
            "+00:00", "Z"
        )
        self.calls.append(
            UsageCallRecord(
                label=label,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=input_tokens + output_tokens,
                cost_usd=cost_usd,
                timestamp=ts,
                pricing_known=pricing_known,
            )
        )


def _load_pricing_table() -> dict[str, dict[str, float]]:
    raw = os.environ.get("ANTHROPIC_PRICING_JSON", "").strip()
    if not raw:
        return dict(DEFAULT_MODEL_PRICING)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.warning("ANTHROPIC_PRICING_JSON invalid (%s); using defaults", e)
        return dict(DEFAULT_MODEL_PRICING)
    if not isinstance(data, dict):
        logger.warning("ANTHROPIC_PRICING_JSON must be a JSON object; using defaults")
        return dict(DEFAULT_MODEL_PRICING)
    out = dict(DEFAULT_MODEL_PRICING)
    for model, rates in data.items():
        if not isinstance(model, str) or not isinstance(rates, dict):
            continue
        inp = rates.get("input_per_million")
        outp = rates.get("output_per_million")
        if isinstance(inp, (int, float)) and isinstance(outp, (int, float)):
            out[model.strip()] = {
                "input_per_million": float(inp),
                "output_per_million": float(outp),
            }
    return out


def _resolve_model_pricing(
    model: str, pricing: dict[str, dict[str, float]]
) -> tuple[dict[str, float] | None, str | None]:
    """Return (rates, matched_key)."""
    if model in pricing:
        return pricing[model], model
    prefix_matches = [key for key in pricing if model.startswith(key)]
    if prefix_matches:
        best = max(prefix_matches, key=len)
        return pricing[best], best
    return None, None


def estimate_cost_usd(
    model: str,
    input_tokens: int,
    output_tokens: int,
    *,
    pricing: dict[str, dict[str, float]] | None = None,
) -> tuple[float | None, bool]:
    """Return (cost_usd, pricing_known)."""
    table = pricing if pricing is not None else _load_pricing_table()
    rates, matched = _resolve_model_pricing(model, table)
    if rates is None:
        return None, False
    cost = (input_tokens / 1_000_000.0) * rates["input_per_million"] + (
        output_tokens / 1_000_000.0
    ) * rates["output_per_million"]
    return round(cost, 6), True


def extract_usage_from_message(msg: Any) -> dict[str, int]:
    usage = getattr(msg, "usage", None)
    if usage is None:
        return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    inp = int(getattr(usage, "input_tokens", 0) or 0)
    outp = int(getattr(usage, "output_tokens", 0) or 0)
    return {
        "input_tokens": inp,
        "output_tokens": outp,
        "total_tokens": inp + outp,
    }


def begin_run(session: str, *, dry_run: bool = False) -> RunUsageCollector:
    collector = RunUsageCollector(session=session, dry_run=dry_run)
    _current_run.set(collector)
    return collector


def get_current_run() -> RunUsageCollector | None:
    return _current_run.get()


def record_call(
    *,
    label: str,
    model: str,
    message: Any,
) -> UsageCallRecord | None:
    """Record one Anthropic Messages API call on the active run collector."""
    collector = _current_run.get()
    if collector is None:
        return None

    usage = extract_usage_from_message(message)
    inp = usage["input_tokens"]
    outp = usage["output_tokens"]
    cost, known = estimate_cost_usd(model, inp, outp)

    if not known:
        logger.warning(
            "Anthropic usage: no pricing for model %r (label=%s); cost omitted",
            model,
            label,
        )

    collector.add_call(
        label=label,
        model=model,
        input_tokens=inp,
        output_tokens=outp,
        cost_usd=cost if known else None,
        pricing_known=known,
    )
    return collector.calls[-1]


def _format_cost(cost: float | None, pricing_known: bool) -> str:
    if not pricing_known or cost is None:
        return "n/a"
    return f"${cost:.4f}"


def log_run_summary(collector: RunUsageCollector) -> None:
    if not collector.calls:
        logger.info("Anthropic usage summary: no API calls recorded this run")
        return

    lines = ["Anthropic usage summary:"]
    for call in collector.calls:
        lines.append(
            f"  - {call.label}: input={call.input_tokens}, "
            f"output={call.output_tokens}, cost_usd={_format_cost(call.cost_usd, call.pricing_known)}"
        )
    lines.append(
        f"  Total: input={collector.total_input_tokens}, "
        f"output={collector.total_output_tokens}, "
        f"tokens={collector.total_tokens}, "
        f"estimated_cost_usd=${collector.total_cost_usd:.4f}"
    )
    logger.info("\n".join(lines))


def default_usage_dir() -> Path:
    return Path.cwd().resolve() / "data" / "usage"


def persist_run_usage(
    collector: RunUsageCollector,
    *,
    output_dir: Path | None = None,
) -> Path | None:
    if not collector.calls:
        return None

    out_dir = output_dir or default_usage_dir()
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = out_dir / f"{date_str}_{collector.session}_usage.jsonl"

    row = {
        "date": date_str,
        "session": collector.session,
        "dry_run": collector.dry_run,
        "recorded_at": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "calls": [asdict(c) for c in collector.calls],
        "totals": {
            "input_tokens": collector.total_input_tokens,
            "output_tokens": collector.total_output_tokens,
            "total_tokens": collector.total_tokens,
            "estimated_cost_usd": round(collector.total_cost_usd, 6),
        },
    }

    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False))
            f.write("\n")
    except OSError as e:
        logger.warning("Failed to persist Anthropic usage to %s: %s", path, e)
        return None
    return path


def end_run(*, persist: bool = True) -> None:
    collector = _current_run.get()
    if collector is None:
        return
    try:
        log_run_summary(collector)
        if persist:
            saved = persist_run_usage(collector)
            if saved:
                logger.info("Anthropic usage saved to %s", saved)
    finally:
        _current_run.set(None)
