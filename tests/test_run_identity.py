from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from swingtrade.run_identity import derive_run_id, enrich_run_identity


def test_derive_run_id_format():
    rid = derive_run_id(
        run_timestamp_utc="2026-06-05T14:33:25Z",
        date="2026-06-05",
        session="pre_market",
    )
    assert rid == "2026-06-05_pre_market_143325Z"


def test_derive_run_id_post_market():
    rid = derive_run_id(
        run_timestamp_utc="2026-05-29T21:15:00Z",
        date="2026-05-29",
        session="post_market",
    )
    assert rid == "2026-05-29_post_market_211500Z"


def test_enrich_run_identity_backfills_from_timestamp():
    row = enrich_run_identity(
        {
            "run_timestamp_utc": "2026-06-05T14:33:25Z",
            "date": "2026-06-05",
            "session": "pre_market",
        }
    )
    assert row["run_id"] == "2026-06-05_pre_market_143325Z"


def test_enrich_run_identity_preserves_existing_run_id():
    row = enrich_run_identity(
        {
            "run_timestamp_utc": "2026-06-05T14:33:25Z",
            "run_id": "custom_run_id",
            "date": "2026-06-05",
            "session": "pre_market",
        }
    )
    assert row["run_id"] == "custom_run_id"


def test_enrich_run_identity_no_timestamp_is_noop():
    row = enrich_run_identity({"date": "2026-06-05", "session": "pre_market"})
    assert "run_id" not in row or not row.get("run_id")


if __name__ == "__main__":
    tests = [
        test_derive_run_id_format,
        test_derive_run_id_post_market,
        test_enrich_run_identity_backfills_from_timestamp,
        test_enrich_run_identity_preserves_existing_run_id,
        test_enrich_run_identity_no_timestamp_is_noop,
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
