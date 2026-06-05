from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from swingtrade.trade_math import (
    MIN_BUY_RISK_REWARD,
    OPPORTUNITY_BUY_NOW,
    OPPORTUNITY_NEAR_BUY,
    OPPORTUNITY_NO_ZONE,
    OPPORTUNITY_PULLBACK_REQUIRED,
    apply_trade_math_to_row,
    calculate_long_rr_at_entry,
    calculate_long_trade_math,
    calculate_opportunity_zone,
    enrich_candidate_trade_fields,
    gate_buy_decision,
    long_entry_ref,
    max_valid_long_entry,
    parse_entry_zone_zones,
    snapshot_ta_audit_fields,
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


def test_max_valid_long_entry_formula():
    assert max_valid_long_entry(95, 120) == 102.14
    assert max_valid_long_entry(2000, 2350) == 2100.0
    assert max_valid_long_entry(1810, 2050) == 1878.57


def test_klac_opportunity_pullback_required():
    row = apply_trade_math_to_row(
        {
            "direction": "Long",
            "entry_zone": "1910.00 - 1935.00",
            "stop_loss": 1810,
            "target": 2050,
            "risk_reward": 2.6,
            "decision": "WATCH",
        }
    )
    assert row["opportunity_status"] == OPPORTUNITY_PULLBACK_REQUIRED
    assert abs(float(row["valid_entry_max"]) - 1878.57) < 0.02
    assert abs(float(row["current_rr"]) - 0.92) < 0.02
    assert row["entry_improvement_needed"] is not None
    assert "Needs entry <=" in row["opportunity_note"]


def test_near_buy_opportunity_classification():
    zone = calculate_opportunity_zone(
        entry_zone=102.6,
        stop_loss=95,
        target=120,
        math_valid=True,
        current_rr=2.33,
    )
    assert zone["opportunity_status"] == OPPORTUNITY_NEAR_BUY
    assert "near threshold" in zone["opportunity_note"]


def test_invalid_levels_no_actionable_zone():
    row = apply_trade_math_to_row(
        {
            "direction": "Long",
            "entry_zone": "100-102",
            "stop_loss": 105,
            "target": 120,
        }
    )
    assert row["opportunity_status"] == OPPORTUNITY_NO_ZONE


def test_parse_entry_zone_zones_midpoint():
    zones = parse_entry_zone_zones("2100.00 - 2135.00")
    assert zones["zone_low"] == 2100.0
    assert zones["zone_high"] == 2135.0
    assert zones["zone_mid"] == 2117.5


def test_rr_at_zone_low_mid_high():
    row = apply_trade_math_to_row(
        {
            "direction": "Long",
            "entry_zone": "2100.00 - 2135.00",
            "stop_loss": 2050,
            "target": 2280,
        }
    )
    assert row["rr_at_zone_low"] == 3.6
    assert abs(float(row["rr_at_zone_mid"]) - 2.41) < 0.02
    assert abs(float(row["rr_at_zone_high"]) - 1.71) < 0.02


def test_planned_entry_uses_valid_entry_max_for_pullback():
    row = apply_trade_math_to_row(
        {
            "direction": "Long",
            "entry_zone": "2100.00 - 2135.00",
            "stop_loss": 2050,
            "target": 2280,
            "decision": "WATCH",
        }
    )
    assert row["opportunity_status"] == OPPORTUNITY_PULLBACK_REQUIRED
    assert row["conditional_buy_limit"] is True
    assert abs(float(row["planned_entry_price"]) - float(row["valid_entry_max"])) < 0.01
    assert float(row["planned_entry_rr"]) == MIN_BUY_RISK_REWARD
    assert row["decision"] == "WATCH"
    assert float(row["qty_for_1000_notional"]) == round(
        1000 / float(row["planned_entry_price"]), 2
    )
    assert float(row["risk_per_share_at_planned_entry"]) == round(
        float(row["planned_entry_price"]) - 2050, 2
    )
    assert float(row["reward_per_share_at_planned_entry"]) == round(
        2280 - float(row["planned_entry_price"]), 2
    )


def test_planned_entry_does_not_upgrade_buy_decision():
    row = gate_buy_decision(
        {
            "decision": "BUY",
            "direction": "Long",
            "entry_zone": "2100.00 - 2135.00",
            "stop_loss": 2050,
            "target": 2280,
            "risk_reward": 3.0,
        }
    )
    assert row["decision"] == "WATCH"
    assert row.get("conditional_buy_limit") is True


def test_invalid_geometry_skips_planned_entry_fields():
    row = apply_trade_math_to_row(
        {
            "direction": "Long",
            "entry_zone": "100-102",
            "stop_loss": 105,
            "target": 120,
        }
    )
    assert row.get("planned_entry_price") is None
    assert row.get("conditional_buy_limit") is False
    assert row.get("zone_low") is None


def test_non_long_skips_planned_entry_fields():
    row = apply_trade_math_to_row(
        {
            "direction": "Short",
            "entry_zone": "100-102",
            "stop_loss": 95,
            "target": 80,
        }
    )
    assert row.get("planned_entry_price") is None
    assert row.get("conditional_buy_limit") is False


def test_calculate_long_rr_at_entry_formula():
    assert calculate_long_rr_at_entry(2117.5, 2050, 2280) == 2.4074


def test_pass_preserves_ta_geometry_and_revisit_fields():
    ta_row = {
        "strategy_match": "Pullback",
        "suggested_entry_zone": "100-102",
        "suggested_stop_loss": 95,
        "suggested_target": 120,
        "risk_reward": 2.0,
    }
    record = {
        "ticker": "TEST",
        "decision": "PASS",
        "direction": None,
        "strategy": "No Clean Setup",
        "entry_zone": None,
        "stop_loss": None,
        "target": None,
    }
    record.update(snapshot_ta_audit_fields(ta_row))
    enriched = enrich_candidate_trade_fields(record)
    assert enriched["decision"] == "PASS"
    assert enriched["ta_math_valid"] is True
    assert enriched["ta_entry_zone"] == "100-102"
    assert enriched["ta_stop_loss"] == 95
    assert enriched["ta_target"] == 120
    assert enriched.get("revisit_opportunity_status") in (
        OPPORTUNITY_PULLBACK_REQUIRED,
        OPPORTUNITY_NEAR_BUY,
        OPPORTUNITY_BUY_NOW,
    )
    assert enriched.get("revisit_planned_entry_price") is not None


def test_pass_with_ta_geometry_does_not_become_buy_or_watch():
    ta_row = {
        "suggested_entry_zone": "100-102",
        "suggested_stop_loss": 95,
        "suggested_target": 120,
    }
    record = {
        "decision": "PASS",
        "direction": None,
        "strategy": "No Clean Setup",
    }
    record.update(snapshot_ta_audit_fields(ta_row))
    enriched = enrich_candidate_trade_fields(record)
    assert enriched["decision"] == "PASS"
    gated = gate_buy_decision(enriched)
    assert gated["decision"] == "PASS"


def test_invalid_pass_row_remains_no_actionable_zone():
    record = {
        "decision": "PASS",
        "direction": None,
        "strategy": "No Clean Setup",
        "ta_math_valid": False,
        "ta_entry_zone": None,
    }
    enriched = enrich_candidate_trade_fields(record)
    assert enriched.get("revisit_opportunity_status") is None
    assert enriched.get("opportunity_status") in (OPPORTUNITY_NO_ZONE, None, "")


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
        test_max_valid_long_entry_formula,
        test_klac_opportunity_pullback_required,
        test_near_buy_opportunity_classification,
        test_invalid_levels_no_actionable_zone,
        test_model_rr_overridden_when_calculated_differs,
        test_parse_entry_zone_zones_midpoint,
        test_rr_at_zone_low_mid_high,
        test_planned_entry_uses_valid_entry_max_for_pullback,
        test_planned_entry_does_not_upgrade_buy_decision,
        test_invalid_geometry_skips_planned_entry_fields,
        test_non_long_skips_planned_entry_fields,
        test_calculate_long_rr_at_entry_formula,
        test_pass_preserves_ta_geometry_and_revisit_fields,
        test_pass_with_ta_geometry_does_not_become_buy_or_watch,
        test_invalid_pass_row_remains_no_actionable_zone,
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
