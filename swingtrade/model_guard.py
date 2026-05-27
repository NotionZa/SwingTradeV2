from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from swingtrade.integrations.anthropic_client import make_anthropic_client
from swingtrade.settings import Settings

logger = logging.getLogger(__name__)

DEFAULT_POLICY: dict[str, Any] = {
    "approved_models": {
        "haiku": ["claude-haiku-4-5-20251001"],
        "sonnet": ["claude-sonnet-4-6"],
        "opus": ["claude-opus-4-7"],
    },
    "blocked_patterns": [
        "claude-opus-4",
        "claude-opus-4-1",
        "claude-sonnet-4",
        "claude-3-7-sonnet",
        "claude-3-5-sonnet",
        "claude-3-opus",
        "claude-3-haiku",
        "claude-2",
    ],
    "allow_unknown_models": False,
    "allow_deprecated_models": False,
    "model_status_cache_max_age_days": 7,
}


@dataclass
class ModelCheckEntry:
    family: str
    model_id: str
    status: str
    reason: str
    warning: str | None = None


@dataclass
class ValidationResult:
    ok: bool
    entries: list[ModelCheckEntry] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    cache_path: Path | None = None
    cache_age_days: float | None = None


def _env_bool(name: str, default: bool = False) -> bool:
    v = os.environ.get(name, "").strip().lower()
    if not v:
        return default
    return v in ("1", "true", "yes", "on")


def configured_model_map(settings: Settings) -> dict[str, str]:
    models: dict[str, str] = {}
    field_names: set[str] = set()
    model_fields = getattr(settings, "model_fields", None)
    if isinstance(model_fields, dict):
        field_names.update(str(k) for k in model_fields.keys())
    field_names.update(str(k) for k in settings.__dict__.keys())
    for field_name in field_names:
        if not field_name.startswith("anthropic_model_"):
            continue
        family = field_name.replace("anthropic_model_", "")
        model_id = getattr(settings, field_name, "")
        if isinstance(model_id, str) and model_id.strip():
            models[family] = model_id.strip()
    return models


def _policy_path(settings: Settings) -> Path:
    return settings.swingtrade_config_dir / "model_policy.yaml"


def _cache_path() -> Path:
    return Path.cwd().resolve() / "data" / "model_status" / "anthropic_models_cache.json"


def load_model_policy(settings: Settings | None = None) -> dict[str, Any]:
    policy = json.loads(json.dumps(DEFAULT_POLICY))
    if settings is None:
        return policy
    p = _policy_path(settings)
    if not p.exists():
        return policy
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("Model policy file unreadable (%s): %s", p, e)
        return policy
    if not isinstance(data, dict):
        logger.warning("Model policy file is not a mapping: %s", p)
        return policy
    for key in ("approved_models", "blocked_patterns", "blocked_models"):
        if key in data:
            policy[key] = data[key]
    for key in (
        "allow_unknown_models",
        "allow_deprecated_models",
        "model_status_cache_max_age_days",
    ):
        if key in data:
            policy[key] = data[key]
    if "blocked_models" in policy and "blocked_patterns" not in policy:
        policy["blocked_patterns"] = policy["blocked_models"]
    if "blocked_models" in policy:
        del policy["blocked_models"]
    return policy


def _approved_for_family(policy: dict[str, Any], family: str) -> list[str]:
    ap = policy.get("approved_models", {})
    if not isinstance(ap, dict):
        return []
    vals = ap.get(family)
    if not isinstance(vals, list):
        return []
    return [str(x).strip() for x in vals if isinstance(x, str) and str(x).strip()]


def _blocked_patterns(policy: dict[str, Any]) -> list[str]:
    vals = policy.get("blocked_patterns")
    if not isinstance(vals, list):
        return []
    return [str(x).strip() for x in vals if isinstance(x, str) and str(x).strip()]


def _match_blocked_pattern(model_id: str, pattern: str) -> bool:
    """Blocked matching: exact or prefix family block."""
    if model_id == pattern:
        return True
    return model_id.startswith(pattern)


def resolve_model_policy_status(
    model_id: str,
    family: str,
    policy: dict[str, Any],
) -> tuple[str, str]:
    """Return (status, reason) where status is approved|blocked|unknown."""
    approved = _approved_for_family(policy, family)
    if model_id in approved:
        return "approved", f"approved for {family}"

    blocked = _blocked_patterns(policy)
    matches = [p for p in blocked if _match_blocked_pattern(model_id, p)]
    if matches:
        best = max(matches, key=len)
        return "blocked", f"matches blocked pattern {best}"

    return "unknown", "not explicitly approved"


def _load_cache() -> tuple[dict[str, Any] | None, Path]:
    p = _cache_path()
    if not p.exists():
        return None, p
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None, p
    if not isinstance(data, dict):
        return None, p
    return data, p


def _cache_model_ids(cache: dict[str, Any] | None) -> set[str]:
    if not cache:
        return set()
    models = cache.get("models")
    if not isinstance(models, list):
        return set()
    out: set[str] = set()
    for m in models:
        if isinstance(m, str) and m.strip():
            out.add(m.strip())
        elif isinstance(m, dict):
            mid = m.get("id")
            if isinstance(mid, str) and mid.strip():
                out.add(mid.strip())
    return out


def _cache_age_days(cache: dict[str, Any] | None) -> float | None:
    if not cache:
        return None
    ts = cache.get("fetched_at_utc")
    if not isinstance(ts, str):
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    age = datetime.now(timezone.utc) - dt.astimezone(timezone.utc)
    return age.total_seconds() / 86400.0


def validate_configured_models(settings: Settings) -> ValidationResult:
    policy = load_model_policy(settings)
    allow_unknown = _env_bool(
        "SWINGTRADE_ALLOW_UNKNOWN_MODELS",
        bool(policy.get("allow_unknown_models", False)),
    )
    allow_deprecated = _env_bool(
        "SWINGTRADE_ALLOW_DEPRECATED_MODELS",
        bool(policy.get("allow_deprecated_models", False)),
    )
    strict_cache = _env_bool("SWINGTRADE_STRICT_MODEL_CACHE", False)

    result = ValidationResult(ok=True)
    cache, cache_path = _load_cache()
    result.cache_path = cache_path
    result.cache_age_days = _cache_age_days(cache)
    cache_ids = _cache_model_ids(cache)

    if allow_unknown:
        result.warnings.append(
            "SWINGTRADE_ALLOW_UNKNOWN_MODELS=1 is enabled; unknown models allowed"
        )
    if allow_deprecated:
        result.warnings.append(
            "SWINGTRADE_ALLOW_DEPRECATED_MODELS=1 is enabled; blocked models allowed"
        )

    max_age = int(policy.get("model_status_cache_max_age_days", 7))
    if cache is None:
        result.warnings.append(
            f"Model cache not found at {cache_path}; validation uses local policy only"
        )
    elif result.cache_age_days is not None and result.cache_age_days > max_age:
        result.warnings.append(
            f"Model cache is stale ({result.cache_age_days:.1f}d > {max_age}d)"
        )

    models = configured_model_map(settings)
    for family, model_id in sorted(models.items()):
        status, reason = resolve_model_policy_status(model_id, family, policy)
        entry = ModelCheckEntry(family=family, model_id=model_id, status=status, reason=reason)

        if status == "approved":
            if cache_ids and model_id not in cache_ids:
                msg = f"Approved model {model_id} missing from cache list"
                if strict_cache:
                    result.errors.append(msg)
                else:
                    result.warnings.append(msg)
        elif status == "blocked":
            if allow_deprecated:
                entry.warning = "blocked model allowed by override"
                result.warnings.append(
                    f"Blocked model allowed by SWINGTRADE_ALLOW_DEPRECATED_MODELS=1: {model_id}"
                )
            else:
                result.errors.append(f"Blocked model not allowed: {model_id} ({reason})")
        else:  # unknown
            if allow_unknown:
                entry.warning = "unknown model allowed by override"
                result.warnings.append(
                    f"Unknown model allowed by SWINGTRADE_ALLOW_UNKNOWN_MODELS=1: {model_id}"
                )
            else:
                result.errors.append(f"Unknown/unapproved model not allowed: {model_id}")

        result.entries.append(entry)

    if result.errors:
        result.ok = False
        raise ValueError(
            "Anthropic model validation failed:\n- " + "\n- ".join(result.errors)
        )
    return result


def refresh_model_status_cache(settings: Settings) -> dict[str, Any]:
    """Refresh local cache from Anthropic models list endpoint (no completions)."""
    client = make_anthropic_client(settings)
    resp = client.models.list()
    items: list[Any]
    if hasattr(resp, "data"):
        items = list(getattr(resp, "data") or [])
    elif isinstance(resp, list):
        items = resp
    else:
        items = list(resp) if resp is not None else []

    model_ids: list[str] = []
    for item in items:
        mid = None
        if isinstance(item, dict):
            mid = item.get("id")
        else:
            mid = getattr(item, "id", None)
        if isinstance(mid, str) and mid.strip():
            model_ids.append(mid.strip())

    model_ids = sorted(set(model_ids))
    payload = {
        "provider": "anthropic",
        "fetched_at_utc": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "models": model_ids,
        "configured_models": configured_model_map(settings),
        "warnings": [],
        "errors": [],
    }

    p = _cache_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def check_models(settings: Settings, *, refresh: bool = False) -> ValidationResult:
    if refresh:
        refresh_model_status_cache(settings)
    return validate_configured_models(settings)
