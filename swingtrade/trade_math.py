from __future__ import annotations

import re
from typing import Any

MIN_BUY_RISK_REWARD = 2.5
NEAR_BUY_RR_LOWER = 2.2
MATH_MISMATCH_WARN_DELTA = 0.2

OPPORTUNITY_BUY_NOW = "BUY_NOW"
OPPORTUNITY_NEAR_BUY = "NEAR_BUY"
OPPORTUNITY_PULLBACK_REQUIRED = "PULLBACK_REQUIRED"
OPPORTUNITY_TARGET_EXTENSION = "TARGET_EXTENSION_REQUIRED"
OPPORTUNITY_NO_ZONE = "NO_ACTIONABLE_ZONE"

_OPPORTUNITY_FIELD_KEYS = (
    "opportunity_status",
    "required_rr",
    "valid_entry_max",
    "current_rr",
    "rr_gap",
    "entry_improvement_needed",
    "opportunity_note",
)

_NUM_RE = re.compile(r"[\d,]+\.?\d*")


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return float(int(value))
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            return float(s.replace(",", ""))
        except ValueError:
            return None
    return None


def _parse_numbers(text: str) -> list[float]:
    nums: list[float] = []
    for token in _NUM_RE.findall(text.replace(",", "")):
        try:
            nums.append(float(token))
        except ValueError:
            continue
    return nums


def parse_entry_zone(entry: Any) -> tuple[float | None, float | None]:
    """Return (low, high) for an entry zone string or numeric entry."""
    if entry is None:
        return None, None
    if isinstance(entry, (int, float)):
        v = float(entry)
        return v, v
    if not isinstance(entry, str):
        entry = str(entry)
    s = entry.strip()
    if not s:
        return None, None
    nums = _parse_numbers(s)
    if not nums:
        return None, None
    if len(nums) == 1:
        return nums[0], nums[0]
    low, high = min(nums), max(nums)
    return low, high


def _is_long_direction(direction: Any) -> bool:
    d = str(direction or "Long").strip().upper()
    return d in ("", "LONG", "L")


def long_entry_ref(entry: Any, *, direction: Any = "Long") -> float | None:
    """Worst-case entry for long validation (high end of zone)."""
    low, high = parse_entry_zone(entry)
    if low is None or high is None:
        return None
    if _is_long_direction(direction):
        return high
    return low


def calculate_long_trade_math(
    *,
    entry_zone: Any,
    stop_loss: Any,
    target: Any,
    direction: Any = "Long",
    model_risk_reward: Any = None,
) -> dict[str, Any]:
    """Deterministic long R/R from entry zone, stop, and target."""
    model_rr = _as_float(model_risk_reward)
    entry_ref = long_entry_ref(entry_zone, direction=direction)
    stop = _as_float(stop_loss)
    tgt = _as_float(target)

    out: dict[str, Any] = {
        "math_valid": False,
        "entry_ref": entry_ref,
        "model_risk_reward": model_rr,
        "calculated_risk_reward": None,
        "math_warning": None,
    }

    if not _is_long_direction(direction):
        out["math_warning"] = "Non-long direction; trade math not validated."
        return out

    if entry_ref is None or stop is None or tgt is None:
        missing = []
        if entry_ref is None:
            missing.append("entry")
        if stop is None:
            missing.append("stop")
        if tgt is None:
            missing.append("target")
        out["math_warning"] = (
            f"Missing or unparseable trade levels: {', '.join(missing)}."
        )
        return out

    risk = entry_ref - stop
    reward = tgt - entry_ref
    if risk <= 0:
        out["math_warning"] = (
            f"Invalid long geometry: stop ({stop:g}) must be below entry_ref ({entry_ref:g})."
        )
        return out
    if reward <= 0:
        out["math_warning"] = (
            f"Invalid long geometry: target ({tgt:g}) must be above entry_ref ({entry_ref:g})."
        )
        return out

    calculated = reward / risk
    out["math_valid"] = True
    out["calculated_risk_reward"] = round(calculated, 4)

    if model_rr is not None and abs(model_rr - calculated) > MATH_MISMATCH_WARN_DELTA:
        out["math_warning"] = (
            f"Model R/R {model_rr:.2f} vs calculated {calculated:.2f} "
            f"(entry_ref={entry_ref:g})."
        )

    return out


def max_valid_long_entry(
    stop_loss: float,
    target: float,
    *,
    required_rr: float = MIN_BUY_RISK_REWARD,
) -> float | None:
    """Maximum long entry price that still achieves *required_rr* with fixed stop/target."""
    if target <= stop_loss:
        return None
    return round((target + required_rr * stop_loss) / (required_rr + 1), 2)


def calculate_opportunity_zone(
    *,
    entry_zone: Any,
    stop_loss: Any,
    target: Any,
    direction: Any = "Long",
    math_valid: bool,
    current_rr: Any = None,
    required_rr: float = MIN_BUY_RISK_REWARD,
) -> dict[str, Any]:
    """Classify actionable long opportunity from validated trade levels."""
    out: dict[str, Any] = {
        "opportunity_status": OPPORTUNITY_NO_ZONE,
        "required_rr": required_rr,
        "valid_entry_max": None,
        "current_rr": None,
        "rr_gap": None,
        "entry_improvement_needed": None,
        "opportunity_note": "No actionable long zone from current stop/target geometry.",
    }

    if not math_valid or not _is_long_direction(direction):
        return out

    stop = _as_float(stop_loss)
    tgt = _as_float(target)
    entry_ref = long_entry_ref(entry_zone, direction=direction)
    rr = _as_float(current_rr)

    if stop is None or tgt is None or entry_ref is None:
        out["opportunity_note"] = "No actionable long zone from current stop/target geometry."
        return out

    max_entry = max_valid_long_entry(stop, tgt, required_rr=required_rr)
    out["valid_entry_max"] = max_entry

    if rr is not None:
        out["current_rr"] = round(rr, 2)
        if rr < required_rr:
            out["rr_gap"] = round(required_rr - rr, 2)

    if rr is not None and rr >= required_rr:
        out["opportunity_status"] = OPPORTUNITY_BUY_NOW
        out["opportunity_note"] = (
            f"Current R/R {rr:.2f} meets the {required_rr:g} threshold at entry_ref {entry_ref:g}."
        )
        out["rr_gap"] = 0.0
        return out

    if rr is not None and NEAR_BUY_RR_LOWER <= rr < required_rr:
        out["opportunity_status"] = OPPORTUNITY_NEAR_BUY
        out["opportunity_note"] = (
            f"Current R/R {rr:.2f} is near threshold; small pullback may qualify."
        )
        if max_entry is not None and max_entry < entry_ref:
            out["entry_improvement_needed"] = round(entry_ref - max_entry, 2)
        return out

    if max_entry is None or max_entry <= stop:
        out["opportunity_status"] = OPPORTUNITY_TARGET_EXTENSION
        out["opportunity_note"] = (
            f"Target {tgt:g} is too close to stop {stop:g}; raise target to improve R/R."
        )
        return out

    if max_entry < entry_ref:
        out["opportunity_status"] = OPPORTUNITY_PULLBACK_REQUIRED
        out["entry_improvement_needed"] = round(entry_ref - max_entry, 2)
        out["opportunity_note"] = (
            f"Needs entry <= {max_entry:.2f} to reach R/R {required_rr:g} "
            f"using stop {stop:g} and target {tgt:g}."
        )
        return out

    out["opportunity_status"] = OPPORTUNITY_TARGET_EXTENSION
    out["opportunity_note"] = (
        f"Cannot reach R/R {required_rr:g} via pullback alone; target extension may be required."
    )
    return out


def _clear_opportunity_fields(row: dict[str, Any]) -> None:
    for key in _OPPORTUNITY_FIELD_KEYS:
        row.pop(key, None)


def apply_opportunity_zone_to_row(row: dict[str, Any]) -> dict[str, Any]:
    """Add opportunity-zone fields after trade math."""
    out = dict(row)
    entry, stop, target = _resolve_trade_levels(out)
    math_valid = bool(out.get("math_valid"))
    current_rr = _as_float(out.get("risk_reward"))

    zone = calculate_opportunity_zone(
        entry_zone=entry,
        stop_loss=stop,
        target=target,
        direction=out.get("direction"),
        math_valid=math_valid,
        current_rr=current_rr,
    )

    _clear_opportunity_fields(out)
    for key in _OPPORTUNITY_FIELD_KEYS:
        val = zone.get(key)
        if val is not None:
            out[key] = val
    return out


def _resolve_trade_levels(row: dict[str, Any]) -> tuple[Any, Any, Any]:
    entry = row.get("entry_zone")
    if entry is None:
        entry = row.get("suggested_entry_zone")
    stop = row.get("stop_loss")
    if stop is None:
        stop = row.get("suggested_stop_loss")
    target = row.get("target")
    if target is None:
        target = row.get("suggested_target")
    return entry, stop, target


def _model_risk_reward_from_row(row: dict[str, Any]) -> float | None:
    """Original model R/R before deterministic override (preserve on re-apply)."""
    existing = _as_float(row.get("model_risk_reward"))
    if existing is not None:
        return existing
    return _as_float(row.get("risk_reward"))


def apply_trade_math_to_row(row: dict[str, Any]) -> dict[str, Any]:
    """Enrich row with validated R/R; override risk_reward when math is valid."""
    out = dict(row)
    entry, stop, target = _resolve_trade_levels(out)
    model_rr = _model_risk_reward_from_row(out)

    result = calculate_long_trade_math(
        entry_zone=entry,
        stop_loss=stop,
        target=target,
        direction=out.get("direction"),
        model_risk_reward=model_rr,
    )

    if model_rr is not None:
        out["model_risk_reward"] = model_rr

    out["math_valid"] = bool(result.get("math_valid"))
    entry_ref = result.get("entry_ref")
    if entry_ref is not None:
        out["entry_ref"] = entry_ref

    warning = result.get("math_warning")
    if isinstance(warning, str) and warning.strip():
        out["math_warning"] = warning.strip()
    else:
        out.pop("math_warning", None)

    calculated = result.get("calculated_risk_reward")
    if out["math_valid"] and calculated is not None:
        out["risk_reward"] = round(float(calculated), 2)
    elif not out["math_valid"]:
        # Do not trust model R/R when levels are invalid.
        out.pop("risk_reward", None)

    return apply_opportunity_zone_to_row(out)


CANDIDATE_TRADE_ENRICHMENT_KEYS = (
    "risk_reward",
    "model_risk_reward",
    "math_valid",
    "math_warning",
    "entry_ref",
    "opportunity_status",
    "required_rr",
    "valid_entry_max",
    "current_rr",
    "rr_gap",
    "entry_improvement_needed",
    "opportunity_note",
)


def enrich_candidate_trade_fields(record: dict[str, Any]) -> dict[str, Any]:
    """Merge deterministic trade math + opportunity fields onto a candidate row."""
    out = dict(record)
    enriched = apply_trade_math_to_row(out)
    out["math_valid"] = bool(enriched.get("math_valid"))
    for key in CANDIDATE_TRADE_ENRICHMENT_KEYS:
        if key == "math_valid":
            continue
        if key in enriched:
            out[key] = enriched[key]
        elif key == "math_warning":
            out.pop("math_warning", None)
    return out


def _append_gate_reason(row: dict[str, Any], suffix: str) -> None:
    prev = str(row.get("reason") or "").strip()
    tag = suffix.strip()
    if not tag:
        return
    if tag in prev:
        return
    row["reason"] = f"{prev} [{tag}]" if prev else tag


def gate_buy_decision(row: dict[str, Any]) -> dict[str, Any]:
    """Apply trade math and downgrade BUY when math invalid or R/R below minimum."""
    out = apply_trade_math_to_row(row)
    dec = str(out.get("decision") or "").strip().upper()
    if dec != "BUY":
        return out

    rr = _as_float(out.get("risk_reward"))
    if not out.get("math_valid"):
        out["decision"] = "WATCH"
        out["decision_math_gate"] = "BUY_downgraded_invalid_math"
        _append_gate_reason(
            out,
            "BUY blocked: trade math invalid (entry/stop/target).",
        )
        return out

    if rr is None or rr < MIN_BUY_RISK_REWARD:
        out["decision"] = "WATCH"
        out["decision_math_gate"] = "BUY_downgraded_rr_below_min"
        rr_disp = f"{rr:.2f}" if rr is not None else "n/a"
        _append_gate_reason(
            out,
            f"BUY blocked: calculated R/R {rr_disp} below {MIN_BUY_RISK_REWARD}.",
        )
    return out


def apply_trade_math_to_cio_structured(structured: dict[str, Any]) -> dict[str, Any]:
    """Validate trade math and gate BUY on all CIO decision rows."""
    decisions = structured.get("decisions")
    if not isinstance(decisions, list):
        return structured

    gated = [
        gate_buy_decision(item) if isinstance(item, dict) else item
        for item in decisions
    ]
    out = {**structured, "decisions": gated}
    summary = out.get("summary")
    if isinstance(summary, dict):
        buy = watch = passed = blocked = 0
        for item in gated:
            if not isinstance(item, dict):
                continue
            dec = str(item.get("decision") or "").strip().upper()
            if dec == "BUY":
                buy += 1
            elif dec == "WATCH":
                watch += 1
            elif dec == "PASS":
                passed += 1
            elif dec == "BLOCKED":
                blocked += 1
        out["summary"] = {
            **summary,
            "buy_count": buy,
            "watch_count": watch,
            "pass_count": passed,
            "blocked_count": blocked,
        }
    return out


def apply_trade_math_to_technical_structured(structured: dict[str, Any]) -> dict[str, Any]:
    """Recalculate risk_reward on each TA ticker row before score caps."""
    if not isinstance(structured, dict):
        return structured
    tickers_in = structured.get("tickers")
    if not isinstance(tickers_in, dict):
        return structured

    tickers: dict[str, Any] = {}
    for sym, row in tickers_in.items():
        if isinstance(row, dict):
            tickers[str(sym)] = apply_trade_math_to_row(row)
        else:
            tickers[str(sym)] = row
    return {**structured, "tickers": tickers}
