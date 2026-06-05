from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from swingtrade.agents.hard_veto import format_hard_veto_discord


def test_hard_veto_footer_when_zero_killed():
    rows = [
        {"symbol": "NVDA", "killed": False, "on_watchlist": True, "reasons": []},
        {"symbol": "AMD", "killed": False, "on_watchlist": True, "reasons": []},
    ]
    md = format_hard_veto_discord(rows, killed_watchlist=[])
    assert "No hard-veto exclusions this run" in md
    assert "eligible for downstream review" in md


def test_hard_veto_footer_when_killed():
    rows = [
        {"symbol": "NVDA", "killed": True, "on_watchlist": True, "reasons": ["price_below_5"]},
        {"symbol": "AMD", "killed": False, "on_watchlist": True, "reasons": []},
    ]
    md = format_hard_veto_discord(rows, killed_watchlist=["NVDA"])
    assert "Skipped downstream" in md
    assert "excluded from downstream TA / Sentiment / CIO this run" in md


if __name__ == "__main__":
    tests = [
        test_hard_veto_footer_when_zero_killed,
        test_hard_veto_footer_when_killed,
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
