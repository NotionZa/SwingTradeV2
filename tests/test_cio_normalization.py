"""Local-only tests for CIO normalization (no API calls)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from swingtrade.agents.cio import (
    _complete_cio_decisions_to_pool,
    _fallback_cio_discord_from_decisions,
    _filter_cio_decisions_to_pool,
    _normalize_cio_structured,
    build_cio_risk_markdown,
)
from swingtrade.models.agents import AgentResult


def test_expected_structured_decisions():
    raw = {
        "discord_markdown": "",
        "structured": {"decisions": [{"ticker": "NVDA", "decision": "BUY"}]},
    }
    structured, shape = _normalize_cio_structured(raw)
    assert shape == "structured.decisions"
    assert structured["decisions"][0]["ticker"] == "NVDA"


def test_top_level_decisions_array():
    raw = {
        "discord_markdown": "",
        "structured": {"summary": {"session": "pre_market"}},
        "decisions": [{"symbol": "AMD", "decision": "WATCH"}],
    }
    structured, shape = _normalize_cio_structured(raw)
    assert shape == "top_level.decisions"
    assert structured["decisions"][0]["ticker"] == "AMD"


def test_single_top_level_decision_object():
    raw = {"ticker": "KLAC", "decision": "BUY", "direction": "Long"}
    structured, shape = _normalize_cio_structured(raw)
    assert shape == "top_level.decision_object"
    assert len(structured["decisions"]) == 1
    assert structured["decisions"][0]["ticker"] == "KLAC"


def test_top_level_list_of_decisions():
    raw = [
        {"ticker": "NVDA", "decision": "BUY"},
        {"ticker": "MSFT", "decision": "WATCH"},
        "noise",
    ]
    structured, shape = _normalize_cio_structured(raw)
    assert shape == "top_level.list"
    assert [d["ticker"] for d in structured["decisions"]] == ["NVDA", "MSFT"]


def test_filter_drops_outside_pool_and_blank():
    structured = {
        "decisions": [
            {"ticker": "NVDA", "decision": "BUY"},
            {"ticker": "TSLA", "decision": "BUY"},
            {"ticker": "", "decision": "WATCH"},
        ]
    }
    out = _filter_cio_decisions_to_pool(structured, ["NVDA"])
    assert [d["ticker"] for d in out["decisions"]] == ["NVDA"]


def test_fallback_markdown_non_empty():
    decisions = [
        {"ticker": "NVDA", "decision": "BUY"},
        {"ticker": "AMD", "decision": "WATCH"},
        {"ticker": "MSFT", "decision": "PASS"},
    ]
    md = _fallback_cio_discord_from_decisions(decisions, "pre_market")
    assert "CIO Decision Brief" in md
    assert "`NVDA`" in md


def test_completion_adds_watch_fallback_for_missing_pool_rows():
    raw = {"ticker": "KLAC", "decision": "BUY", "direction": "Long", "cio_score": 8.2}
    structured, _shape = _normalize_cio_structured(raw)
    structured = _filter_cio_decisions_to_pool(structured, ["KLAC", "NVDA", "AMD"])
    completed, missing = _complete_cio_decisions_to_pool(
        structured, ["KLAC", "NVDA", "AMD"]
    )
    assert missing == ["NVDA", "AMD"]
    assert [d["ticker"] for d in completed["decisions"]] == ["KLAC", "NVDA", "AMD"]
    assert completed["decisions"][0]["decision"] == "BUY"
    assert completed["decisions"][1]["decision"] == "WATCH"
    assert completed["decisions"][2]["decision"] == "WATCH"
    assert completed["decisions"][1]["source"] == "system_fallback"
    assert completed["decisions"][2]["source"] == "system_fallback"


def test_completion_still_drops_symbols_outside_pool():
    raw = {
        "decisions": [
            {"ticker": "KLAC", "decision": "BUY"},
            {"ticker": "TSLA", "decision": "BUY"},
        ]
    }
    structured, _shape = _normalize_cio_structured(raw)
    filtered = _filter_cio_decisions_to_pool(structured, ["KLAC", "NVDA", "AMD"])
    completed, _missing = _complete_cio_decisions_to_pool(
        filtered, ["KLAC", "NVDA", "AMD"]
    )
    assert [d["ticker"] for d in completed["decisions"]] == ["KLAC", "NVDA", "AMD"]
    assert all(d["ticker"] != "TSLA" for d in completed["decisions"])


def test_fallback_markdown_counts_reflect_completed_decisions():
    decisions = [
        {"ticker": "KLAC", "decision": "BUY"},
        {"ticker": "NVDA", "decision": "WATCH", "source": "system_fallback"},
        {"ticker": "AMD", "decision": "WATCH", "source": "system_fallback"},
    ]
    md = _fallback_cio_discord_from_decisions(decisions, "pre_market")
    assert "**🟢 BUY** (1)" in md
    assert "**🟡 WATCH** (2)" in md


def test_risk_summary_uses_completed_decisions_when_summary_counts_invalid():
    structured = {
        "summary": {
            "overall_risk_level": "Moderate",
            "market_regime": "choppy",
            "tech_bias": "selective",
            "buy_count": 0,
            "watch_count": 0,
            "pass_count": 0,
            "blocked_count": 0,
        },
        "decisions": [
            {"ticker": "KLAC", "decision": "BUY", "cio_score": 7.8},
            {"ticker": "NVDA", "decision": "WATCH", "source": "system_fallback"},
            {"ticker": "AMD", "decision": "WATCH", "source": "system_fallback"},
        ],
    }
    result = AgentResult(
        agent_id="cio",
        discord_markdown="",
        structured=structured,
        model_used="claude-opus-4-7",
    )
    md = build_cio_risk_markdown(result, "pre_market")
    assert "BUY: 1" in md
    assert "WATCH: 2" in md
    assert "PASS: 0" in md
    assert "BLOCKED: 0" in md
    assert "Highest Conviction:** KLAC" in md


if __name__ == "__main__":
    tests = [
        test_expected_structured_decisions,
        test_top_level_decisions_array,
        test_single_top_level_decision_object,
        test_top_level_list_of_decisions,
        test_filter_drops_outside_pool_and_blank,
        test_fallback_markdown_non_empty,
        test_completion_adds_watch_fallback_for_missing_pool_rows,
        test_completion_still_drops_symbols_outside_pool,
        test_fallback_markdown_counts_reflect_completed_decisions,
        test_risk_summary_uses_completed_decisions_when_summary_counts_invalid,
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

