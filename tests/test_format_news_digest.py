from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from swingtrade.pipeline import (
    MARKET_NEWS_DISCORD_MAX_HEADLINES_PER_TICKER,
    format_news_digest,
)


def _structured(per_ticker: dict) -> dict:
    return {"raw_bundle": {"per_ticker": per_ticker}}


def test_caps_headlines_per_ticker():
    articles = [
        {"headline": f"H{i}", "source": "Yahoo"}
        for i in range(MARKET_NEWS_DISCORD_MAX_HEADLINES_PER_TICKER + 2)
    ]
    md = format_news_digest(_structured({"NVDA": {"news": articles}}))
    assert md.count("- H") == MARKET_NEWS_DISCORD_MAX_HEADLINES_PER_TICKER
    assert "- H3" not in md


def test_preserves_source_labels():
    md = format_news_digest(
        _structured(
            {
                "AMD": {
                    "news": [
                        {"headline": "Chip rally", "source": "Benzinga"},
                        {"headline": "Supply watch", "source": "SeekingAlpha"},
                    ]
                }
            }
        )
    )
    assert "_(Benzinga)_" in md
    assert "_(SeekingAlpha)_" in md


def test_skips_zero_headline_tickers():
    md = format_news_digest(
        _structured(
            {
                "EMPTY": {"news": []},
                "MSFT": {"news": [{"headline": "Cloud beat", "source": "Yahoo"}]},
            }
        )
    )
    assert "`EMPTY`" not in md
    assert "`MSFT`" in md
    assert "_(no headlines)_" not in md


def test_includes_digest_heading_and_footer():
    md = format_news_digest(_structured({}))
    assert "**Market news digest** (headlines only)" in md
    assert "Showing up to 3 headlines per ticker" in md
    assert "full raw headlines remain available in structured output." in md


if __name__ == "__main__":
    tests = [
        test_caps_headlines_per_ticker,
        test_preserves_source_labels,
        test_skips_zero_headline_tickers,
        test_includes_digest_heading_and_footer,
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
