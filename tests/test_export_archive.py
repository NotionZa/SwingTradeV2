from __future__ import annotations

import csv
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from swingtrade.export_archive import (
    archive_path_for_run,
    archive_suffix_from_stem,
    resolve_run_id_from_records,
    write_archive_snapshot,
)


def test_archive_suffix_from_stem():
    assert archive_suffix_from_stem("2026-06-08_pre_market_review") == "review"
    assert (
        archive_suffix_from_stem("2026-06-08_pre_market_review_outcomes")
        == "review_outcomes"
    )
    assert (
        archive_suffix_from_stem("2026-06-08_pre_market_opportunities_outcomes")
        == "opportunities_outcomes"
    )
    assert archive_suffix_from_stem("signals_outcomes") is None


def test_archive_path_for_run():
    latest = Path("data/reviews/2026-06-08_pre_market_review.csv")
    archive = archive_path_for_run(latest, "2026-06-08_pre_market_133641Z")
    assert archive == Path(
        "data/reviews/archive/2026-06-08_pre_market_133641Z_review.csv"
    )


def test_resolve_run_id_from_records_derives_from_timestamp():
    rid = resolve_run_id_from_records(
        [
            {
                "run_timestamp_utc": "2026-06-08T13:36:41Z",
                "date": "2026-06-08",
                "session": "pre_market",
            }
        ]
    )
    assert rid == "2026-06-08_pre_market_133641Z"


def test_write_archive_snapshot_overwrites_same_run_file(tmp_path: Path):
    latest = tmp_path / "2026-06-08_pre_market_review.csv"
    latest.write_text("version=1\n", encoding="utf-8")
    records = [
        {
            "run_id": "2026-06-08_pre_market_133641Z",
            "run_timestamp_utc": "2026-06-08T13:36:41Z",
            "date": "2026-06-08",
            "session": "pre_market",
            "ticker": "NVDA",
        }
    ]
    archive = write_archive_snapshot(latest, records)
    assert archive is not None
    assert archive.read_text(encoding="utf-8") == "version=1\n"

    latest.write_text("version=2\n", encoding="utf-8")
    archive2 = write_archive_snapshot(latest, records)
    assert archive2 == archive
    assert archive.read_text(encoding="utf-8") == "version=2\n"


def test_write_archive_snapshot_skips_without_run_id(tmp_path: Path):
    latest = tmp_path / "2026-06-08_pre_market_review.csv"
    latest.write_text("a,b\n1,2\n", encoding="utf-8")
    archive = write_archive_snapshot(latest, [{"ticker": "NVDA", "date": "2026-06-08"}])
    assert archive is None
    assert not (tmp_path / "archive").exists()


if __name__ == "__main__":
    tests = [
        test_archive_suffix_from_stem,
        test_archive_path_for_run,
        test_resolve_run_id_from_records_derives_from_timestamp,
        test_write_archive_snapshot_overwrites_same_run_file,
        test_write_archive_snapshot_skips_without_run_id,
    ]
    failed = 0
    for t in tests:
        try:
            if "tmp_path" in t.__code__.co_varnames:
                with tempfile.TemporaryDirectory() as td:
                    t(Path(td))
            else:
                t()
            print(f"PASS {t.__name__}")
        except Exception as e:
            failed += 1
            print(f"FAIL {t.__name__}: {e}")
    raise SystemExit(1 if failed else 0)
