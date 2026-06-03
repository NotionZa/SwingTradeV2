from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from swingtrade.trade_math import (
    MIN_BUY_RISK_REWARD,
    apply_trade_math_to_row,
    calculate_long_trade_math,
    gate_buy_decision,
    long_entry_ref,
)


def test_klac_worst_case_rr_and_buy_blocked():
    row = {
        "ticker": "KLAC",
        "decision": "BUY",
        "direction": "Long",
        "entry_zone": "1910.00 - 1935.00",
        "stop_loss": 1810,
        "target": 2050,
        "risk_reward": 2.6,
    }
    math = calculate_long_trade_math(
        entry_zone=row["entry_zone"],
        stop_loss=row["stop_loss"],
        target=row["target"],
        model_risk_reward=row["risk_reward"],
    )
    assert long_entry_ref(row["entry_zone"]) == 1935.0
    assert math["math_valid"] is True
    assert abs(float(math["calculated_risk_reward"]) - 0.92) < 0.01
    assert math.get("math_warning")

    gated = gate_buy_decision(row)
    assert gated["decision"] == "WATCH"
    assert gated["model_risk_reward"] == 2.6
    assert abs(float(gated["risk_reward"]) - 0.92) < 0.01
    assert gated.get("decision_math_gate") == "BUY_downgraded_rr_below_min"


def test_valid_long_zone_may_remain_buy():
    row = {
        "decision": "BUY",
        "direction": "Long",
        "entry_zone": "100-102",
        "stop_loss": 95,
        "target": 120,
        "risk_reward": 2.0,
    }
    gated = gate_buy_decision(row)
    assert gated["math_valid"] is True
    assert float(gated["risk_reward"]) >= MIN_BUY_RISK_REWARD
    assert gated["decision"] == "BUY"
    assert gated["model_risk_reward"] == 2.0
    assert gated.get("math_warning")


def test_invalid_long_stop_above_entry_blocks_buy():
    row = {
        "decision": "BUY",
        "direction": "Long",
        "entry_zone": "100-102",
        "stop_loss": 105,
        "target": 120,
        "risk_reward": 3.0,
    }
    gated = gate_buy_decision(row)
    assert gated["math_valid"] is False
    assert gated["decision"] == "WATCH"
    assert gated.get("decision_math_gate") == "BUY_downgraded_invalid_math"
    assert gated.get("math_warning")
    assert "risk_reward" not in gated or gated.get("risk_reward") is None


def test_reapply_preserves_model_risk_reward_not_calculated():
    first = apply_trade_math_to_row(
        {
            "entry_zone": "100-102",
            "stop_loss": 95,
            "target": 120,
            "risk_reward": 4.5,
        }
    )
    second = apply_trade_math_to_row(first)
    assert second["model_risk_reward"] == 4.5
    assert abs(float(second["risk_reward"]) - 2.57) < 0.05


def test_invalid_long_sets_math_warning():
    row = apply_trade_math_to_row(
        {
            "direction": "Long",
            "entry_zone": "100-102",
            "stop_loss": 105,
            "target": 120,
            "risk_reward": 3.0,
        }
    )
    assert row["math_valid"] is False
    assert row["model_risk_reward"] == 3.0
    assert "stop" in str(row.get("math_warning", "")).lower()


def test_model_rr_overridden_when_calculated_differs():
    row = apply_trade_math_to_row(
        {
            "entry_zone": "100-102",
            "stop_loss": 95,
            "target": 120,
            "risk_reward": 4.5,
        }
    )
    assert row["math_valid"] is True
    assert abs(float(row["risk_reward"]) - 2.57) < 0.05
    assert row["model_risk_reward"] == 4.5
    assert row.get("math_warning")


if __name__ == "__main__":
    tests = [
        test_klac_worst_case_rr_and_buy_blocked,
        test_valid_long_zone_may_remain_buy,
        test_invalid_long_stop_above_entry_blocks_buy,
        test_reapply_preserves_model_risk_reward_not_calculated,
        test_invalid_long_sets_math_warning,
        test_model_rr_overridden_when_calculated_differs,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except Exception as e:
            failed += 1
            print(f"FAIL {t.__name__}: {e}")
    raise SystemExit(1 if failed else 0)
