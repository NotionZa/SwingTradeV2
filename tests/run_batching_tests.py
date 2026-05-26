"""Standalone test runner for analysis_batching — no external test framework needed."""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

# Allow `swingtrade` imports when run from repo root or tests/
sys.path.insert(0, str(Path(__file__).parent.parent))

from swingtrade.analysis_batching import (
    DEFAULT_ANALYSIS_BATCH_SIZE,
    chunk_symbols,
    merge_sentiment_structured,
    merge_technical_structured,
)

_PASSED = 0
_FAILED = 0


def _run(name: str, fn) -> None:
    global _PASSED, _FAILED
    try:
        fn()
        print(f"  PASS  {name}")
        _PASSED += 1
    except Exception:
        print(f"  FAIL  {name}")
        traceback.print_exc()
        _FAILED += 1


def t_chunk_empty():
    assert chunk_symbols([], 15) == []


def t_chunk_one():
    assert chunk_symbols(["A"], 15) == [["A"]]


def t_chunk_exact_one_batch():
    syms = [f"T{i}" for i in range(15)]
    batches = chunk_symbols(syms, 15)
    assert len(batches) == 1
    assert len(batches[0]) == 15


def t_chunk_16():
    syms = [f"T{i}" for i in range(16)]
    batches = chunk_symbols(syms, 15)
    assert len(batches) == 2
    assert len(batches[0]) == 15
    assert len(batches[1]) == 1


def t_chunk_30():
    syms = [f"T{i}" for i in range(30)]
    batches = chunk_symbols(syms, 15)
    assert len(batches) == 2
    assert all(len(b) == 15 for b in batches)


def t_chunk_45():
    syms = [f"T{i}" for i in range(45)]
    batches = chunk_symbols(syms, 15)
    assert len(batches) == 3
    assert sum(len(b) for b in batches) == 45


def t_chunk_order():
    syms = ["NVDA", "AMD", "MSFT", "AAPL"]
    batches = chunk_symbols(syms, 2)
    assert batches == [["NVDA", "AMD"], ["MSFT", "AAPL"]]


def t_merge_technical():
    parts = [
        {
            "tickers": {"NVDA": {"ticker": "NVDA", "ta_score": 8.0}},
            "scores": {"NVDA": 8.0},
            "inputs": {"NVDA": {"features": {"last_close": 100.0}}},
            "notes": "Batch one note.",
        },
        {
            "tickers": {"AMD": {"ticker": "AMD", "ta_score": 7.0}},
            "scores": {"AMD": 7.0},
            "inputs": {"AMD": {"features": {"last_close": 50.0}}},
            "notes": "Batch two note.",
        },
    ]
    merged = merge_technical_structured(parts, batch_count=2, symbol_count=2, batch_size=15)
    assert set(merged["tickers"].keys()) == {"NVDA", "AMD"}
    assert merged["scores"]["NVDA"] == 8.0
    assert merged["scores"]["AMD"] == 7.0
    assert "NVDA" in merged["inputs"]
    assert "AMD" in merged["inputs"]
    assert "Batch 1/2" in merged["notes"]
    assert "Batch 2/2" in merged["notes"]
    assert merged["_batching"]["batches"] == 2
    assert merged["_batching"]["symbols"] == 2


def t_merge_technical_no_overlap():
    # Symbols must not be duplicated
    parts = [
        {"tickers": {"NVDA": {"ta_score": 8.0}}, "scores": {"NVDA": 8.0}, "inputs": {}, "notes": ""},
        {"tickers": {"NVDA": {"ta_score": 9.0}}, "scores": {"NVDA": 9.0}, "inputs": {}, "notes": ""},
    ]
    merged = merge_technical_structured(parts, batch_count=2, symbol_count=2, batch_size=15)
    # Last-write wins; NVDA should appear once
    assert list(merged["tickers"].keys()).count("NVDA") == 1


def t_merge_technical_scores_sync():
    # Scores must be synced from tickers.ta_score
    parts = [
        {"tickers": {"TSLA": {"ta_score": 6.5}}, "scores": {}, "inputs": {}, "notes": ""},
    ]
    merged = merge_technical_structured(parts, batch_count=1, symbol_count=1, batch_size=15)
    assert merged["scores"]["TSLA"] == 6.5


def t_merge_sentiment():
    parts = [
        {
            "macro": {"score_0_10": 6, "catalyst": "Risk-on"},
            "per_ticker": {"NVDA": {"score_0_10": 7, "catalyst": "Earnings beat"}},
            "raw_bundle": {"per_ticker": {"NVDA": {"symbol": "NVDA", "news": [{"headline": "h1"}]}}},
        },
        {
            "macro": {"score_0_10": 4, "catalyst": "Rates rising"},
            "per_ticker": {"AMD": {"score_0_10": 5, "catalyst": "Supply chain"}},
            "raw_bundle": {"per_ticker": {"AMD": {"symbol": "AMD", "news": [{"headline": "h2"}]}}},
        },
    ]
    merged = merge_sentiment_structured(parts, batch_count=2, symbol_count=2)
    assert set(merged["per_ticker"].keys()) == {"NVDA", "AMD"}
    assert merged["macro"]["score_0_10"] == 5  # mean of 6 and 4
    assert "NVDA" in merged["raw_bundle"]["per_ticker"]
    assert "AMD" in merged["raw_bundle"]["per_ticker"]
    assert merged["_batching"]["batches"] == 2


def t_merge_sentiment_macro_single():
    parts = [{"macro": {"score_0_10": 7, "catalyst": "Fed pause"}, "per_ticker": {}, "raw_bundle": {}}]
    merged = merge_sentiment_structured(parts, batch_count=1, symbol_count=1)
    assert merged["macro"]["score_0_10"] == 7
    assert merged["macro"]["catalyst"] == "Fed pause"


def t_default_batch_size():
    assert DEFAULT_ANALYSIS_BATCH_SIZE == 15


if __name__ == "__main__":
    print("=== test_analysis_batching ===")
    for name, fn in [
        ("chunk_symbols: empty", t_chunk_empty),
        ("chunk_symbols: 1", t_chunk_one),
        ("chunk_symbols: exact 15", t_chunk_exact_one_batch),
        ("chunk_symbols: 16 -> 2 batches", t_chunk_16),
        ("chunk_symbols: 30", t_chunk_30),
        ("chunk_symbols: 45 -> 3 batches", t_chunk_45),
        ("chunk_symbols: stable order", t_chunk_order),
        ("merge_technical: keys/scores/inputs", t_merge_technical),
        ("merge_technical: no duplicate keys", t_merge_technical_no_overlap),
        ("merge_technical: scores synced from tickers", t_merge_technical_scores_sync),
        ("merge_sentiment: per_ticker + macro avg", t_merge_sentiment),
        ("merge_sentiment: single batch macro", t_merge_sentiment_macro_single),
        ("DEFAULT_ANALYSIS_BATCH_SIZE == 15", t_default_batch_size),
    ]:
        _run(name, fn)

    print(f"\n{_PASSED} passed, {_FAILED} failed")
    sys.exit(0 if _FAILED == 0 else 1)
