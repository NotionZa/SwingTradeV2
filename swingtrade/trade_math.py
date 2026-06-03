from __future__ import annotations

import re
from typing import Any

MIN_BUY_RISK_REWARD = 2.5
MATH_MISMATCH_WARN_DELTA = 0.2

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
