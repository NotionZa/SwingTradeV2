"""Local tests for Anthropic usage/cost tracking (no API calls)."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from swingtrade.anthropic_usage import (
    RunUsageCollector,
    begin_run,
    end_run,
    estimate_cost_usd,
    extract_usage_from_message,
    persist_run_usage,
    record_call,
)


class _FakeUsage:
    def __init__(self, inp: int, outp: int) -> None:
        self.input_tokens = inp
        self.output_tokens = outp


class _FakeMessage:
    def __init__(self, inp: int, outp: int) -> None:
        self.usage = _FakeUsage(inp, outp)


def test_extract_usage_from_message():
    u = extract_usage_from_message(_FakeMessage(1200, 340))
    assert u["input_tokens"] == 1200
    assert u["output_tokens"] == 340
    assert u["total_tokens"] == 1540


def test_estimate_cost_known_model():
    cost, known = estimate_cost_usd(
        "claude-sonnet-4-6",
        1_000_000,
        1_000_000,
        pricing={
            "claude-sonnet-4-6": {
                "input_per_million": 3.0,
                "output_per_million": 15.0,
            }
        },
    )
    assert known is True
    assert cost == 18.0


def test_estimate_cost_opus_4_7_vs_opus_4():
    cost_47, known_47 = estimate_cost_usd("claude-opus-4-7", 1_000_000, 1_000_000)
    cost_4, known_4 = estimate_cost_usd("claude-opus-4-20250514", 1_000_000, 1_000_000)
    assert known_47 and known_4
    assert cost_47 == 30.0  # 5 + 25
    assert cost_4 == 90.0  # 15 + 75


def test_estimate_cost_unknown_model():
    cost, known = estimate_cost_usd("unknown-model-xyz", 100, 100)
    assert known is False
    assert cost is None


def test_record_call_and_totals():
    begin_run("pre_market", dry_run=True)
    record_call(
        label="technical batch 1/3",
        model="claude-sonnet-4-6",
        message=_FakeMessage(10_000, 2_000),
    )
    record_call(
        label="cio",
        model="claude-opus-4-7",
        message=_FakeMessage(5_000, 1_500),
    )
    from swingtrade.anthropic_usage import get_current_run

    collector = get_current_run()
    assert collector is not None
    assert collector.total_input_tokens == 15_000
    assert collector.total_output_tokens == 3_500
    assert collector.total_tokens == 18_500
    assert len(collector.calls) == 2
    end_run(persist=False)


def test_persist_run_usage_jsonl():
    collector = RunUsageCollector(session="post_market", dry_run=True)
    collector.add_call(
        label="sentiment",
        model="claude-sonnet-4-6",
        input_tokens=100,
        output_tokens=50,
        cost_usd=0.001,
        pricing_known=True,
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = persist_run_usage(collector, output_dir=Path(tmp))
        assert path is not None
        line = path.read_text(encoding="utf-8").strip()
        row = json.loads(line)
        assert row["session"] == "post_market"
        assert len(row["calls"]) == 1
        assert row["totals"]["input_tokens"] == 100


if __name__ == "__main__":
    tests = [
        test_extract_usage_from_message,
        test_estimate_cost_known_model,
        test_estimate_cost_opus_4_7_vs_opus_4,
        test_estimate_cost_unknown_model,
        test_record_call_and_totals,
        test_persist_run_usage_jsonl,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except Exception as e:
            failed += 1
            print(f"FAIL {t.__name__}: {e}")
    sys.exit(1 if failed else 0)
