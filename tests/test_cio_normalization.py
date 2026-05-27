"""Local-only tests for CIO normalization (no API calls)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from swingtrade.agents.cio import (
    _fallback_cio_discord_from_decisions,
    _filter_cio_decisions_to_pool,
    _normalize_cio_structured,
)


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


if __name__ == "__main__":
    tests = [
        test_expected_structured_decisions,
        test_top_level_decisions_array,
        test_single_top_level_decision_object,
        test_top_level_list_of_decisions,
        test_filter_drops_outside_pool_and_blank,
        test_fallback_markdown_non_empty,
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

