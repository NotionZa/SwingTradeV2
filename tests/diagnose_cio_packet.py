"""Local-only CIO packet size diagnostics (no Anthropic / no pipeline run)."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

from swingtrade.cio_packet import (
    CIO_MESSAGE_ACCEPTABLE_CHARS,
    CIO_MESSAGE_FAIL_CHARS,
    CIO_MESSAGE_IDEAL_CHARS,
    assert_local_size_gate,
    diagnose_cio_packet,
)
from swingtrade.models.agents import PipelineState


def _rich_ta_row(sym: str) -> dict:
    return {
        "ticker": sym,
        "ta_score": 7.5,
        "strategy_match": "MOMENTUM",
        "setup_quality": "B",
        "trend_status": "Uptrend",
        "momentum_status": "Strong",
        "relative_strength_vs_qqq": "OUTPERFORMING",
        "suggested_entry_zone": "100-102",
        "suggested_stop_loss": "95",
        "suggested_target": "110",
        "risk_reward": 2.4,
        "summary": "Constructive swing setup with trend alignment. " * 8,
        "cio_notes": "Watch volume confirmation. " * 6,
        "technical_risks": ["Extended move", "Earnings in 2 weeks"],
        "invalidation_conditions": ["Break below 20dma"],
    }


def _fixture(n_analysis: int, n_cio: int) -> PipelineState:
    analysis = [f"SYM{i}" for i in range(n_analysis)]
    cio_syms = analysis[:n_cio]
    ta = {
        "tickers": {s: _rich_ta_row(s) for s in analysis},
        "scores": {s: 7.5 for s in analysis},
        "inputs": {s: {"features": {"last_close": 100, "rsi_14": 55, "volume_ratio": 1.1}} for s in analysis},
        "notes": "Merged batch notes. " * 400,
        "_batching": {"batches": 3, "symbols": n_analysis},
    }
    se = {
        "macro": {"score_0_10": 5, "catalyst": "Rates and tech leadership mixed."},
        "per_ticker": {s: {"score_0_10": 6, "catalyst": "Headline flow " * 10} for s in analysis},
        "raw_bundle": {
            "per_ticker": {
                s: {
                    "news": [{"headline": "Breaking: " + "x" * 120, "source": "Reuters"}] * 5,
                    "reddit": [{"title": "reddit " * 40}] * 4,
                }
                for s in analysis
            }
        },
        "_batching": {"batches": 3},
    }
    hv = {
        "vetoes": [
            {"symbol": s, "killed": i < 5, "reasons": ["price_below_5"] if i < 5 else []}
            for i, s in enumerate(analysis)
        ],
        "survivors": analysis[5:],
        "killed": analysis[:5],
    }
    ms = {
        "regime": "risk_on",
        "trading_bias": "long_tech",
        "confidence_0_10": 7,
        "macro_summary": "Macro backdrop supportive for selective tech longs. " * 30,
        "sector_strength_notes": "Semis leading. " * 20,
        "discord_markdown": "# should not appear in CIO packet",
    }
    state = PipelineState(
        tickers=cio_syms,
        watchlist_by_category={},
        analysis_tickers=analysis,
    )
    state.prior_structured = {
        "technical_analysis": ta,
        "sentiment": se,
        "hard_veto": hv,
        "market_sentiment": ms,
    }
    return state


def _print_diag(label: str, diag: dict[str, Any]) -> None:
    print(f"\n=== {label} ===")
    print(f"  cio_user_message_chars:     {diag['cio_user_message_chars']:,}")
    print(f"  size_band:                  {diag['size_band']}")
    print(f"  cio_candidate_count:        {diag['cio_candidate_count']}")
    print(f"  compact_packet_candidate_count: {diag['compact_packet_candidate_count']}")
    print(f"  gates: ideal<={CIO_MESSAGE_IDEAL_CHARS:,}  acceptable<={CIO_MESSAGE_ACCEPTABLE_CHARS:,}  fail<={CIO_MESSAGE_FAIL_CHARS:,}")
    print("  section_chars:")
    for k, v in diag["section_chars"].items():
        print(f"    {k}: {v:,}")


def main() -> int:
    scenarios = [
        ("12 analysis / 12 CIO", _fixture(12, 12)),
        ("36 analysis / 12 CIO", _fixture(36, 12)),
        ("40 analysis / 12 CIO", _fixture(40, 12)),
    ]
    failed = 0
    for label, state in scenarios:
        diag = state.cio_packet_diagnostics("post_market", cio_symbols=state.tickers)
        _print_diag(label, diag)
        packet = state.build_cio_packet("post_market", cio_symbols=state.tickers)
        assert "inputs" not in str(packet)
        assert "raw_bundle" not in str(packet)
        assert "_batching" not in str(packet)
        try:
            assert_local_size_gate(
                state.prior_structured,
                "post_market",
                cio_symbols=state.tickers,
                analysis_symbols=state.analysis_tickers,
            )
            print("  local size gate: PASS")
        except AssertionError as e:
            print(f"  local size gate: FAIL — {e}")
            failed += 1

    print(f"\n{'All scenarios within fail gate.' if failed == 0 else f'{failed} scenario(s) over fail gate.'}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
