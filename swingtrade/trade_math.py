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

_PLANNED_ENTRY_FIELD_KEYS = (
    "zone_low",
    "zone_mid",
    "zone_high",
    "rr_at_zone_low",
    "rr_at_zone_mid",
    "rr_at_zone_high",
    "planned_entry_price",
    "planned_entry_rr",
    "conditional_buy_limit",
    "qty_for_1000_notional",
    "risk_per_share_at_planned_entry",
    "reward_per_share_at_planned_entry",
)

TA_AUDIT_FIELD_KEYS = (
    "ta_direction",
    "ta_strategy",
    "ta_entry_zone",
    "ta_stop_loss",
    "ta_target",
    "ta_risk_reward",
    "ta_math_valid",
)

_REVISIT_OPPORTUNITY_FIELD_KEYS = tuple(f"revisit_{k}" for k in _OPPORTUNITY_FIELD_KEYS)
_REVISIT_PLANNED_FIELD_KEYS = tuple(f"revisit_{k}" for k in _PLANNED_ENTRY_FIELD_KEYS)

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


def parse_entry_zone_zones(entry: Any) -> dict[str, float | None]:
    """Return zone_low, zone_mid, zone_high for an entry zone."""
    low, high = parse_entry_zone(entry)
    if low is None or high is None:
        return {"zone_low": None, "zone_mid": None, "zone_high": None}
    return {
        "zone_low": low,
        "zone_high": high,
        "zone_mid": round((low + high) / 2, 2),
    }


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


def calculate_long_rr_at_entry(
    entry_price: Any,
    stop_loss: Any,
    target: Any,
) -> float | None:
    """Long R/R at a specific entry: (target - entry) / (entry - stop)."""
    entry = _as_float(entry_price)
    stop = _as_float(stop_loss)
    tgt = _as_float(target)
    if entry is None or stop is None or tgt is None:
        return None
    risk = entry - stop
    reward = tgt - entry
    if risk <= 0 or reward <= 0:
        return None
    return round(reward / risk, 4)


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


def _clear_planned_entry_fields(row: dict[str, Any]) -> None:
    for key in _PLANNED_ENTRY_FIELD_KEYS:
        row.pop(key, None)


def calculate_planned_entry_fields(
    *,
    entry_zone: Any,
    stop_loss: Any,
    target: Any,
    direction: Any = "Long",
    math_valid: bool,
    entry_ref: Any = None,
    current_rr: Any = None,
    required_rr: float = MIN_BUY_RISK_REWARD,
    valid_entry_max: Any = None,
) -> dict[str, Any]:
    """Execution guidance: planned limit entry without changing BUY/WATCH decisions."""
    out: dict[str, Any] = {key: None for key in _PLANNED_ENTRY_FIELD_KEYS}
    out["conditional_buy_limit"] = False

    if not math_valid or not _is_long_direction(direction):
        return out

    stop = _as_float(stop_loss)
    tgt = _as_float(target)
    if stop is None or tgt is None:
        return out

    zones = parse_entry_zone_zones(entry_zone)
    zone_low = zones.get("zone_low")
    zone_mid = zones.get("zone_mid")
    zone_high = zones.get("zone_high")
    if zone_low is not None:
        out["zone_low"] = zone_low
    if zone_mid is not None:
        out["zone_mid"] = zone_mid
    if zone_high is not None:
        out["zone_high"] = zone_high

    rr_low = calculate_long_rr_at_entry(zone_low, stop, tgt)
    rr_mid = calculate_long_rr_at_entry(zone_mid, stop, tgt)
    rr_high = calculate_long_rr_at_entry(zone_high, stop, tgt)
    if rr_low is not None:
        out["rr_at_zone_low"] = round(rr_low, 2)
    if rr_mid is not None:
        out["rr_at_zone_mid"] = round(rr_mid, 2)
    if rr_high is not None:
        out["rr_at_zone_high"] = round(rr_high, 2)

    rr = _as_float(current_rr)
    ref = _as_float(entry_ref)
    vmax = _as_float(valid_entry_max)
    required = float(required_rr)

    if rr is not None and rr >= required and ref is not None:
        out["planned_entry_price"] = ref
        out["planned_entry_rr"] = round(rr, 2)
        out["conditional_buy_limit"] = False
    elif vmax is not None and vmax > stop:
        out["planned_entry_price"] = vmax
        out["planned_entry_rr"] = required
        out["conditional_buy_limit"] = True
    elif rr_mid is not None and rr_mid >= required and zone_mid is not None:
        out["planned_entry_price"] = zone_mid
        out["planned_entry_rr"] = round(rr_mid, 2)
        out["conditional_buy_limit"] = True
    else:
        return out

    planned = _as_float(out.get("planned_entry_price"))
    if planned is not None and planned > stop and tgt > planned:
        out["qty_for_1000_notional"] = round(1000 / planned, 2)
        out["risk_per_share_at_planned_entry"] = round(planned - stop, 2)
        out["reward_per_share_at_planned_entry"] = round(tgt - planned, 2)

    return out


def apply_planned_entry_to_row(row: dict[str, Any]) -> dict[str, Any]:
    """Add planned-entry / conditional-limit execution fields after opportunity zone."""
    out = dict(row)
    entry, stop, target = _resolve_trade_levels(out)
    required = _as_float(out.get("required_rr")) or MIN_BUY_RISK_REWARD

    planned = calculate_planned_entry_fields(
        entry_zone=entry,
        stop_loss=stop,
        target=target,
        direction=out.get("direction"),
        math_valid=bool(out.get("math_valid")),
        entry_ref=out.get("entry_ref"),
        current_rr=out.get("current_rr") if out.get("current_rr") is not None else out.get("risk_reward"),
        required_rr=required,
        valid_entry_max=out.get("valid_entry_max"),
    )

    _clear_planned_entry_fields(out)
    for key in _PLANNED_ENTRY_FIELD_KEYS:
        if key == "conditional_buy_limit":
            out[key] = bool(planned.get(key))
            continue
        val = planned.get(key)
        if val is not None:
            out[key] = val
    return out


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

    return apply_planned_entry_to_row(apply_opportunity_zone_to_row(out))


def snapshot_ta_audit_fields(ta_row: dict[str, Any]) -> dict[str, Any]:
    """Preserve pre-CIO technical geometry for audit and PASS revisit planning."""
    if not isinstance(ta_row, dict):
        return {}
    entry = ta_row.get("suggested_entry_zone")
    stop = ta_row.get("suggested_stop_loss")
    target = ta_row.get("suggested_target")
    strategy = ta_row.get("strategy_match")
    enriched = apply_trade_math_to_row(
        {
            "direction": "Long",
            "entry_zone": entry,
            "stop_loss": stop,
            "target": target,
            "risk_reward": ta_row.get("risk_reward"),
            "model_risk_reward": ta_row.get("model_risk_reward"),
        }
    )
    out: dict[str, Any] = {
        "ta_direction": "Long",
        "ta_strategy": strategy,
        "ta_entry_zone": entry,
        "ta_stop_loss": stop,
        "ta_target": target,
        "ta_math_valid": bool(enriched.get("math_valid")),
    }
    if enriched.get("risk_reward") is not None:
        out["ta_risk_reward"] = enriched["risk_reward"]
    return out


def _clear_revisit_fields(row: dict[str, Any]) -> None:
    for key in _REVISIT_OPPORTUNITY_FIELD_KEYS + _REVISIT_PLANNED_FIELD_KEYS:
        row.pop(key, None)


def _should_apply_revisit_from_ta(record: dict[str, Any]) -> bool:
    """PASS rows with valid TA long geometry may get revisit_* planning fields."""
    if str(record.get("decision") or "").strip().upper() != "PASS":
        return False
    if not record.get("ta_math_valid"):
        return False
    if (
        record.get("ta_entry_zone") is None
        or record.get("ta_stop_loss") is None
        or record.get("ta_target") is None
    ):
        return False
    final_status = str(record.get("opportunity_status") or "").strip().upper()
    if record.get("math_valid") and final_status and final_status != OPPORTUNITY_NO_ZONE:
        return False
    return True


def apply_revisit_from_ta_audit(record: dict[str, Any]) -> dict[str, Any]:
    """Populate revisit_* opportunity/planned-entry fields from preserved TA geometry."""
    out = dict(record)
    if not _should_apply_revisit_from_ta(out):
        _clear_revisit_fields(out)
        return out

    ta_enriched = apply_planned_entry_to_row(
        apply_opportunity_zone_to_row(
            apply_trade_math_to_row(
                {
                    "direction": out.get("ta_direction") or "Long",
                    "entry_zone": out.get("ta_entry_zone"),
                    "stop_loss": out.get("ta_stop_loss"),
                    "target": out.get("ta_target"),
                    "risk_reward": out.get("ta_risk_reward"),
                }
            )
        )
    )

    for key in _OPPORTUNITY_FIELD_KEYS:
        revisit_key = f"revisit_{key}"
        val = ta_enriched.get(key)
        if val is not None:
            out[revisit_key] = val

    for key in _PLANNED_ENTRY_FIELD_KEYS:
        revisit_key = f"revisit_{key}"
        if key == "conditional_buy_limit":
            out[revisit_key] = bool(ta_enriched.get(key))
        else:
            val = ta_enriched.get(key)
            if val is not None:
                out[revisit_key] = val

    return out


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
    "zone_low",
    "zone_mid",
    "zone_high",
    "rr_at_zone_low",
    "rr_at_zone_mid",
    "rr_at_zone_high",
    "planned_entry_price",
    "planned_entry_rr",
    "conditional_buy_limit",
    "qty_for_1000_notional",
    "risk_per_share_at_planned_entry",
    "reward_per_share_at_planned_entry",
    *TA_AUDIT_FIELD_KEYS,
    *_REVISIT_OPPORTUNITY_FIELD_KEYS,
    *_REVISIT_PLANNED_FIELD_KEYS,
)


def enrich_candidate_trade_fields(record: dict[str, Any]) -> dict[str, Any]:
    """Merge deterministic trade math + opportunity + revisit fields onto a candidate row."""
    out = dict(record)
    enriched = apply_trade_math_to_row(out)
    out["math_valid"] = bool(enriched.get("math_valid"))
    for key in CANDIDATE_TRADE_ENRICHMENT_KEYS:
        if key in TA_AUDIT_FIELD_KEYS:
            continue
        if key in _REVISIT_OPPORTUNITY_FIELD_KEYS or key in _REVISIT_PLANNED_FIELD_KEYS:
            continue
        if key == "math_valid":
            continue
        if key in enriched:
            out[key] = enriched[key]
        elif key == "math_warning":
            out.pop("math_warning", None)
    return apply_revisit_from_ta_audit(out)


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
