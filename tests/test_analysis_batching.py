from __future__ import annotations

from swingtrade.analysis_batching import (
    DEFAULT_ANALYSIS_BATCH_SIZE,
    chunk_symbols,
    merge_sentiment_structured,
    merge_technical_structured,
)


def test_chunk_symbols_empty():
    assert chunk_symbols([], 15) == []


def test_chunk_symbols_sizes():
    symbols = [f"T{i}" for i in range(45)]
    assert len(chunk_symbols(symbols[:1], 15)) == 1
    assert len(chunk_symbols(symbols[:15], 15)) == 1
    assert len(chunk_symbols(symbols[:16], 15)) == 2
    assert len(chunk_symbols(symbols[:30], 15)) == 2
    assert len(chunk_symbols(symbols, 15)) == 3
    assert sum(len(b) for b in chunk_symbols(symbols, 15)) == 45


def test_chunk_symbols_stable_order():
    symbols = ["NVDA", "AMD", "MSFT", "AAPL"]
    batches = chunk_symbols(symbols, 2)
    assert batches == [["NVDA", "AMD"], ["MSFT", "AAPL"]]


def test_merge_technical_structured():
    parts = [
        {
            "tickers": {"NVDA": {"ticker": "NVDA", "ta_score": 8.0}},
            "scores": {"NVDA": 8.0},
            "inputs": {"NVDA": {"features": {"last_close": 1.0}}},
            "notes": "Batch one note.",
        },
        {
            "tickers": {"AMD": {"ticker": "AMD", "ta_score": 7.0}},
            "scores": {"AMD": 7.0},
            "inputs": {"AMD": {"features": {"last_close": 2.0}}},
            "notes": "Batch two note.",
        },
    ]
    merged = merge_technical_structured(
        parts, batch_count=2, symbol_count=2, batch_size=15
    )
    assert set(merged["tickers"].keys()) == {"NVDA", "AMD"}
    assert merged["scores"]["NVDA"] == 8.0
    assert merged["scores"]["AMD"] == 7.0
    assert "NVDA" in merged["inputs"]
    assert "Batch 1/2" in merged["notes"]
    assert merged["_batching"]["batches"] == 2


def test_merge_sentiment_structured():
    parts = [
        {
            "macro": {"score_0_10": 6, "catalyst": "Risk-on"},
            "per_ticker": {"NVDA": {"score_0_10": 7, "catalyst": "Earnings"}},
            "raw_bundle": {
                "per_ticker": {
                    "NVDA": {"symbol": "NVDA", "news": [{"headline": "h1"}]}
                }
            },
        },
        {
            "macro": {"score_0_10": 4, "catalyst": "Rates"},
            "per_ticker": {"AMD": {"score_0_10": 5, "catalyst": "Supply"}},
            "raw_bundle": {
                "per_ticker": {
                    "AMD": {"symbol": "AMD", "news": [{"headline": "h2"}]}
                }
            },
        },
    ]
    merged = merge_sentiment_structured(parts, batch_count=2, symbol_count=2)
    assert set(merged["per_ticker"].keys()) == {"NVDA", "AMD"}
    assert merged["macro"]["score_0_10"] == 5
    assert "NVDA" in merged["raw_bundle"]["per_ticker"]
    assert "AMD" in merged["raw_bundle"]["per_ticker"]


def test_default_batch_size():
    assert DEFAULT_ANALYSIS_BATCH_SIZE == 15
