from __future__ import annotations

import csv
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from swingtrade.candidate_review import export_candidate_review_csv
from swingtrade.opportunity_export import (
    OPPORTUNITY_CSV_COLUMNS,
    export_opportunity_csv,
    filter_opportunity_records,
    sort_opportunity_records,
)
from swingtrade.trade_math import (
    OPPORTUNITY_NEAR_BUY,
    OPPORTUNITY_NO_ZONE,
    OPPORTUNITY_PULLBACK_REQUIRED,
    enrich_candidate_trade_fields,
)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False))
            f.write("\n")


def _pullback_row(
    *,
    ticker: str,
    ts: str,
    entry: str = "2080-2130",
    stop: float = 2000,
    target: float = 2350,
    rank_score: float = 0.5,
    rr_gap: float | None = None,
) -> dict:
    row = enrich_candidate_trade_fields(
        {
            "run_timestamp_utc": ts,
            "date": "2026-06-03",
            "session": "post_market",
            "ticker": ticker,
            "review_level": "cio_reviewed",
            "decision": "WATCH",
            "direction": "Long",
            "entry_zone": entry,
            "stop_loss": stop,
            "target": target,
            "risk_reward": 2.5,
            "rank_score": rank_score,
        }
    )
    if rr_gap is not None:
        row["rr_gap"] = rr_gap
    return row


def test_latest_run_only_opportunity_export(tmp_path: Path):
    jsonl = tmp_path / "candidates.jsonl"
    _write_jsonl(
        jsonl,
        [
            _pullback_row(ticker="OLD", ts="2026-06-03T08:00:00Z"),
            _pullback_row(ticker="NEW", ts="2026-06-03T12:00:00Z"),
        ],
    )
    csv_path = export_opportunity_csv(jsonl, output_path=tmp_path / "opp.csv")
    with csv_path.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    assert rows[0]["ticker"] == "NEW"


def test_all_runs_exports_matching_rows(tmp_path: Path):
    jsonl = tmp_path / "candidates.jsonl"
    _write_jsonl(
        jsonl,
        [
            _pullback_row(ticker="A", ts="2026-06-03T08:00:00Z"),
            _pullback_row(ticker="B", ts="2026-06-03T12:00:00Z"),
        ],
    )
    csv_path = export_opportunity_csv(
        jsonl, output_path=tmp_path / "all.csv", all_runs=True
    )
    with csv_path.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 2
    assert {r["ticker"] for r in rows} == {"A", "B"}


def test_pullback_required_has_valid_entry_and_note(tmp_path: Path):
    jsonl = tmp_path / "klac.jsonl"
    _write_jsonl(jsonl, [_pullback_row(ticker="KLAC", ts="2026-06-03T12:00:00Z")])
    csv_path = export_opportunity_csv(jsonl, output_path=tmp_path / "klac.csv")
    with csv_path.open(encoding="utf-8") as f:
        row = next(csv.DictReader(f))
    assert row["opportunity_status"] == OPPORTUNITY_PULLBACK_REQUIRED
    assert row["valid_entry_max"] == "2100.0"
    assert "Needs entry" in row["opportunity_note"]


def test_no_actionable_zone_excluded_by_default(tmp_path: Path):
    jsonl = tmp_path / "mixed.jsonl"
    _write_jsonl(
        jsonl,
        [
            _pullback_row(ticker="KLAC", ts="2026-06-03T12:00:00Z"),
            {
                "run_timestamp_utc": "2026-06-03T12:00:00Z",
                "ticker": "BAD",
                "decision": "WATCH",
                "direction": "Short",
                "entry_zone": "100-102",
                "stop_loss": 95,
                "target": 120,
            },
        ],
    )
    csv_path = export_opportunity_csv(jsonl, output_path=tmp_path / "mixed.csv")
    with csv_path.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    assert rows[0]["ticker"] == "KLAC"


def test_include_no_zone_flag(tmp_path: Path):
    records = [
        enrich_candidate_trade_fields(
            {
                "ticker": "BAD",
                "direction": "Short",
                "entry_zone": "100-102",
                "stop_loss": 95,
                "target": 120,
            }
        ),
        _pullback_row(ticker="KLAC", ts="2026-06-03T12:00:00Z"),
    ]
    filtered = filter_opportunity_records(records, include_no_zone=True)
    statuses = {_normalize_status(r) for r in filtered}
    assert OPPORTUNITY_NO_ZONE in statuses
    assert OPPORTUNITY_PULLBACK_REQUIRED in statuses


def _normalize_status(record: dict) -> str:
    return str(record.get("opportunity_status") or "").upper()


def test_sort_near_buy_before_pullback():
    near = enrich_candidate_trade_fields(
        {
            "ticker": "NEAR",
            "direction": "Long",
            "entry_zone": "102.6",
            "stop_loss": 95,
            "target": 120,
            "rank_score": 0.3,
        }
    )
    pull = _pullback_row(ticker="PULL", ts="2026-06-03T12:00:00Z", rank_score=0.9)
    assert near["opportunity_status"] == OPPORTUNITY_NEAR_BUY
    sorted_rows = sort_opportunity_records([pull, near])
    assert sorted_rows[0]["ticker"] == "NEAR"
    assert sorted_rows[1]["ticker"] == "PULL"


def test_csv_contains_all_expected_columns(tmp_path: Path):
    jsonl = tmp_path / "cols.jsonl"
    _write_jsonl(jsonl, [_pullback_row(ticker="X", ts="2026-06-03T12:00:00Z")])
    csv_path = export_opportunity_csv(jsonl, output_path=tmp_path / "cols.csv")
    with csv_path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        assert list(reader.fieldnames) == list(OPPORTUNITY_CSV_COLUMNS)


def test_review_candidates_unchanged(tmp_path: Path):
    jsonl = tmp_path / "review.jsonl"
    _write_jsonl(
        jsonl,
        [
            {
                "run_timestamp_utc": "2026-06-03T08:00:00Z",
                "ticker": "OLD",
                "decision": "PASS",
            },
            {
                "run_timestamp_utc": "2026-06-03T12:00:00Z",
                "ticker": "NEW",
                "decision": "WATCH",
            },
        ],
    )
    csv_path = export_candidate_review_csv(jsonl, output_path=tmp_path / "review.csv")
    with csv_path.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    assert rows[0]["ticker"] == "NEW"


if __name__ == "__main__":
    tests = [
        test_latest_run_only_opportunity_export,
        test_all_runs_exports_matching_rows,
        test_pullback_required_has_valid_entry_and_note,
        test_no_actionable_zone_excluded_by_default,
        test_include_no_zone_flag,
        test_sort_near_buy_before_pullback,
        test_csv_contains_all_expected_columns,
        test_review_candidates_unchanged,
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
