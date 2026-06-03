"""Local-only tests for CIO normalization (no API calls)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from swingtrade.agents.cio import (
    _CIO_DISCORD_BUY_THESIS_MAX_CHARS,
    _complete_cio_decisions_to_pool,
    _fallback_cio_discord_from_decisions,
    _filter_cio_decisions_to_pool,
    _normalize_cio_structured,
    build_cio_risk_markdown,
)
from swingtrade.trade_math import apply_trade_math_to_cio_structured
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
    structured = {
        "decisions": [
            {"ticker": "NVDA", "decision": "BUY"},
            {"ticker": "AMD", "decision": "WATCH"},
            {"ticker": "MSFT", "decision": "PASS"},
        ],
    }
    md = _fallback_cio_discord_from_decisions(structured, "pre_market")
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
    structured = {
        "decisions": [
            {"ticker": "KLAC", "decision": "BUY"},
            {"ticker": "NVDA", "decision": "WATCH", "source": "system_fallback"},
            {"ticker": "AMD", "decision": "WATCH", "source": "system_fallback"},
        ],
    }
    md = _fallback_cio_discord_from_decisions(structured, "pre_market")
    assert "**BUY Candidates** (1)" in md
    assert "**WATCH Candidates** (2)" in md


def test_fallback_markdown_includes_market_context():
    structured = {
        "summary": {
            "market_regime": "choppy",
            "tech_bias": "selective longs",
            "overall_risk_level": "Medium",
            "session_message": "Stay selective until volume confirms.",
        },
        "decisions": [{"ticker": "NVDA", "decision": "PASS", "reason": "Weak R/R"}],
    }
    md = _fallback_cio_discord_from_decisions(structured, "pre_market")
    assert "Market Context" in md
    assert "choppy" in md
    assert "selective longs" in md
    assert "Medium" in md


def test_fallback_markdown_includes_buy_trade_details():
    structured = {
        "decisions": [
            {
                "ticker": "KLAC",
                "decision": "BUY",
                "direction": "Long",
                "strategy": "Momentum",
                "conviction": "High",
                "entry_zone": "680-685",
                "stop_loss": 665,
                "target": 720,
                "risk_reward": 2.8,
                "technical_thesis": "Pullback held above 20dma with RS improving.",
                "reason": "Clean momentum continuation setup.",
                "invalidation_conditions": ["Close below 665"],
                "action_required": "Enter on open above 682.",
            },
        ],
    }
    md = _fallback_cio_discord_from_decisions(structured, "pre_market")
    assert "BUY Candidates" in md
    assert "Entry:" in md
    assert "680-685" in md
    assert "Stop:" in md
    assert "Target:" in md


def test_fallback_markdown_buy_thesis_preserves_decision_ending():
    """Long BUY thesis keeps decision tail below cap; ellipsis only beyond BUY thesis cap."""
    ending = " DECISION: enter on reclaim of 20dma with volume confirmation."
    filler = "M" * 200
    thesis_within_cap = filler + ending
    assert len(thesis_within_cap) <= _CIO_DISCORD_BUY_THESIS_MAX_CHARS

    md = _fallback_cio_discord_from_decisions(
        {
            "decisions": [
                {
                    "ticker": "META",
                    "decision": "BUY",
                    "technical_thesis": thesis_within_cap,
                    "reason": "Setup valid.",
                }
            ],
        },
        "pre_market",
    )
    assert "DECISION: enter on reclaim of 20dma with volume confirmation." in md
    assert "- **Thesis:**" in md
    thesis_line = next(line for line in md.splitlines() if line.startswith("- **Thesis:**"))
    assert "..." not in thesis_line

    over_cap = "M" * (_CIO_DISCORD_BUY_THESIS_MAX_CHARS + 40) + ending
    md_trunc = _fallback_cio_discord_from_decisions(
        {
            "decisions": [
                {
                    "ticker": "META",
                    "decision": "BUY",
                    "technical_thesis": over_cap,
                }
            ],
        },
        "pre_market",
    )
    thesis_line_trunc = next(
        line for line in md_trunc.splitlines() if line.startswith("- **Thesis:**")
    )
    assert thesis_line_trunc.endswith("...")
    assert ending not in md_trunc


def test_cio_trade_math_downgrades_klac_buy():
    structured = apply_trade_math_to_cio_structured(
        {
            "decisions": [
                {
                    "ticker": "KLAC",
                    "decision": "BUY",
                    "direction": "Long",
                    "entry_zone": "1910.00 - 1935.00",
                    "stop_loss": 1810,
                    "target": 2050,
                    "risk_reward": 2.6,
                }
            ],
            "summary": {"buy_count": 1, "watch_count": 0},
        }
    )
    row = structured["decisions"][0]
    assert row["decision"] == "WATCH"
    assert abs(float(row["risk_reward"]) - 0.92) < 0.01
    assert structured["summary"]["buy_count"] == 0
    assert structured["summary"]["watch_count"] == 1


def test_fallback_markdown_includes_watch_details():
    structured = {
        "decisions": [
            {
                "ticker": "AMD",
                "decision": "WATCH",
                "direction": "Long",
                "strategy": "Pullback",
                "reason": "Structure improving but volume light.",
                "technical_thesis": "Needs breakout above resistance.",
                "revisit_condition": "Upgrade on volume > 20d avg.",
                "action_required": "Wait for confirmation.",
            },
        ],
    }
    md = _fallback_cio_discord_from_decisions(structured, "pre_market")
    assert "WATCH Candidates" in md
    assert "Case:" in md
    assert "Trigger:" in md


def test_fallback_markdown_summarizes_pass_and_blocked():
    structured = {
        "decisions": [
            {"ticker": "META", "decision": "PASS", "reason": "No clean setup."},
            {
                "ticker": "TSLA",
                "decision": "BLOCKED",
                "reason": "Earnings gate.",
                "revisit_condition": "After earnings window.",
            },
        ],
    }
    md = _fallback_cio_discord_from_decisions(structured, "pre_market")
    assert "PASS / BLOCKED Summary" in md
    assert "`META`" in md
    assert "PASS" in md
    assert "`TSLA`" in md
    assert "BLOCKED" in md


def test_fallback_markdown_handles_sparse_system_fallback_watch():
    structured = {
        "decisions": [
            {
                "ticker": "NVDA",
                "decision": "WATCH",
                "source": "system_fallback",
                "reason": "CIO omitted ticker from response.",
                "action_required": "Manual review required before trade.",
            },
        ],
    }
    md = _fallback_cio_discord_from_decisions(structured, "pre_market")
    assert "WATCH Candidates" in md
    assert "`NVDA`" in md
    assert "Reason:" in md
    assert "Action:" in md


def test_risk_summary_counts_always_follow_final_decisions():
    structured = {
        "summary": {
            "overall_risk_level": "Moderate",
            "market_regime": "choppy",
            "tech_bias": "selective",
            "buy_count": 0,
            "watch_count": 5,
            "pass_count": 7,
            "blocked_count": 0,
            "highest_conviction_ticker": "MU",
        },
        "decisions": [
            {"ticker": "AAPL", "decision": "WATCH"},
            {"ticker": "MSFT", "decision": "WATCH"},
            {"ticker": "NVDA", "decision": "WATCH"},
            {"ticker": "AMD", "decision": "WATCH"},
            {"ticker": "KLAC", "decision": "WATCH"},
            {"ticker": "MU", "decision": "WATCH"},
            {"ticker": "META", "decision": "PASS"},
            {"ticker": "AMZN", "decision": "PASS"},
            {"ticker": "AVGO", "decision": "PASS"},
            {"ticker": "QCOM", "decision": "PASS"},
            {"ticker": "TSM", "decision": "PASS"},
            {"ticker": "ASML", "decision": "PASS"},
        ],
    }
    result = AgentResult(
        agent_id="cio",
        discord_markdown="",
        structured=structured,
        model_used="claude-opus-4-7",
    )
    md = build_cio_risk_markdown(result, "pre_market")
    assert "BUY: 0" in md
    assert "WATCH: 6" in md
    assert "PASS: 6" in md
    assert "BLOCKED: 0" in md
    assert "Highest Conviction:** None" in md


def test_risk_summary_highest_conviction_is_highest_scored_buy():
    structured = {
        "summary": {
            "overall_risk_level": "Low",
            "market_regime": "bull_trending",
            "tech_bias": "favorable",
            "highest_conviction_ticker": "SHOULD_NOT_OVERRIDE_BUY_LOGIC",
        },
        "decisions": [
            {"ticker": "NVDA", "decision": "BUY", "cio_score": 7.2},
            {"ticker": "KLAC", "decision": "BUY", "cio_score": 8.6},
            {"ticker": "AMD", "decision": "BUY", "cio_score": 8.1},
            {"ticker": "MSFT", "decision": "WATCH"},
        ],
    }
    result = AgentResult(
        agent_id="cio",
        discord_markdown="",
        structured=structured,
        model_used="claude-opus-4-7",
    )
    md = build_cio_risk_markdown(result, "pre_market")
    assert "BUY: 3" in md
    assert "WATCH: 1" in md
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
        test_fallback_markdown_includes_market_context,
        test_fallback_markdown_includes_buy_trade_details,
        test_fallback_markdown_buy_thesis_preserves_decision_ending,
        test_cio_trade_math_downgrades_klac_buy,
        test_fallback_markdown_includes_watch_details,
        test_fallback_markdown_summarizes_pass_and_blocked,
        test_fallback_markdown_handles_sparse_system_fallback_watch,
        test_risk_summary_counts_always_follow_final_decisions,
        test_risk_summary_highest_conviction_is_highest_scored_buy,
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

