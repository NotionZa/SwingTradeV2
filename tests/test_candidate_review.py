from __future__ import annotations

import csv
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from swingtrade.candidate_logger import _build_cio_reviewed_record
from swingtrade.candidate_review import (
    CSV_COLUMNS,
    enrich_records_for_review_export,
    export_candidate_review_csv,
    select_records_for_review_export,
)
from swingtrade.trade_math import OPPORTUNITY_NO_ZONE, OPPORTUNITY_PULLBACK_REQUIRED


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False))
            f.write("\n")


def test_latest_run_only_exports_newest_timestamp_group():
    records = [
        {"run_timestamp_utc": "2026-05-29T08:00:00Z", "ticker": "NVDA", "decision": "PASS"},
        {"run_timestamp_utc": "2026-05-29T08:00:00Z", "ticker": "AMD", "decision": "PASS"},
        {"run_timestamp_utc": "2026-05-29T12:00:00Z", "ticker": "NVDA", "decision": "WATCH"},
        {"run_timestamp_utc": "2026-05-29T12:00:00Z", "ticker": "MSFT", "decision": "BUY"},
    ]
    selected = select_records_for_review_export(records)
    assert len(selected) == 2
    assert {r["ticker"] for r in selected} == {"NVDA", "MSFT"}
    assert all(r["run_timestamp_utc"] == "2026-05-29T12:00:00Z" for r in selected)
    nvda = next(r for r in selected if r["ticker"] == "NVDA")
    assert nvda["decision"] == "WATCH"


def test_stale_buy_ignored_when_latest_row_is_watch():
    records = [
        {
            "run_timestamp_utc": "2026-05-29T08:00:00Z",
            "ticker": "KLAC",
            "decision": "BUY",
        },
        {
            "run_timestamp_utc": "2026-05-29T12:00:00Z",
            "ticker": "KLAC",
            "decision": "WATCH",
        },
    ]
    selected = select_records_for_review_export(records)
    assert len(selected) == 1
    assert selected[0]["decision"] == "WATCH"


def test_export_count_matches_latest_run_group():
    records = [
        {"run_timestamp_utc": "2026-05-29T08:00:00Z", "ticker": f"T{i}"}
        for i in range(10)
    ] + [
        {"run_timestamp_utc": "2026-05-29T12:00:00Z", "ticker": f"U{i}"}
        for i in range(5)
    ]
    selected = select_records_for_review_export(records)
    assert len(selected) == 5


def test_single_run_behavior_unchanged():
    records = [
        {"run_timestamp_utc": "2026-05-29T12:00:00Z", "ticker": "NVDA", "decision": "BUY"},
        {"run_timestamp_utc": "2026-05-29T12:00:00Z", "ticker": "AMD", "decision": "WATCH"},
    ]
    selected = select_records_for_review_export(records)
    assert len(selected) == 2
    assert selected[0]["ticker"] == "NVDA"
    assert selected[1]["ticker"] == "AMD"


def test_dedupe_keeps_last_row_within_latest_run():
    records = [
        {"run_timestamp_utc": "2026-05-29T12:00:00Z", "ticker": "KLAC", "decision": "BUY"},
        {"run_timestamp_utc": "2026-05-29T12:00:00Z", "ticker": "KLAC", "decision": "WATCH"},
    ]
    selected = select_records_for_review_export(records)
    assert len(selected) == 1
    assert selected[0]["decision"] == "WATCH"


def test_all_runs_exports_every_row():
    records = [
        {"run_timestamp_utc": "2026-05-29T08:00:00Z", "ticker": "KLAC", "decision": "BUY"},
        {"run_timestamp_utc": "2026-05-29T12:00:00Z", "ticker": "KLAC", "decision": "WATCH"},
    ]
    selected = select_records_for_review_export(records, all_runs=True)
    assert len(selected) == 2


def test_export_csv_includes_trade_math_audit_columns(tmp_path: Path):
    jsonl = tmp_path / "math_audit.jsonl"
    _write_jsonl(
        jsonl,
        [
            {
                "run_timestamp_utc": "2026-05-29T12:00:00Z",
                "ticker": "KLAC",
                "decision": "WATCH",
                "direction": "Long",
                "entry_zone": "1910.00 - 1935.00",
                "stop_loss": 1810,
                "target": 2050,
                "risk_reward": 0.92,
                "model_risk_reward": 2.6,
                "math_valid": True,
                "math_warning": "Model R/R 2.60 vs calculated 0.92 (entry_ref=1935).",
            },
        ],
    )
    csv_path = export_candidate_review_csv(jsonl, output_path=tmp_path / "audit.csv")
    with csv_path.open(encoding="utf-8") as f:
        row = next(csv.DictReader(f))
    assert "model_risk_reward" in CSV_COLUMNS
    assert row["model_risk_reward"] == "2.6"
    assert row["math_valid"] == "true"
    assert "Model R/R" in row["math_warning"]
    assert row["risk_reward"] == "0.92"


def test_cio_record_finalize_populates_math_audit_fields():
    record = _build_cio_reviewed_record(
        {
            "ticker": "NVDA",
            "decision": "WATCH",
            "direction": "Long",
            "entry_zone": "2030.00 - 2055.00",
            "stop_loss": 1950,
            "target": 2200,
            "risk_reward": 2.5,
        },
        session="pre_market",
        run_timestamp_utc="2026-05-29T12:00:00Z",
        date="2026-05-29",
        summary={},
        rank_score=0.8,
        analysis_rank=1,
    )
    assert record["math_valid"] is True
    assert record["model_risk_reward"] == 2.5
    assert abs(float(record["risk_reward"]) - 1.38) < 0.05
    assert "Model R/R" in str(record.get("math_warning", ""))


def test_cio_record_includes_run_id_and_timestamp():
    record = _build_cio_reviewed_record(
        {
            "ticker": "NVDA",
            "decision": "WATCH",
            "direction": "Long",
            "entry_zone": "100-102",
            "stop_loss": 95,
            "target": 110,
        },
        session="pre_market",
        run_timestamp_utc="2026-06-05T14:33:25Z",
        date="2026-06-05",
        summary={},
        rank_score=0.8,
        analysis_rank=1,
    )
    assert record["run_timestamp_utc"] == "2026-06-05T14:33:25Z"
    assert record["run_id"] == "2026-06-05_pre_market_143325Z"


def test_export_includes_run_id_backfilled_from_timestamp(tmp_path: Path):
    jsonl = tmp_path / "run_id.jsonl"
    _write_jsonl(
        jsonl,
        [
            {
                "run_timestamp_utc": "2026-06-05T14:33:25Z",
                "date": "2026-06-05",
                "session": "pre_market",
                "ticker": "NVDA",
                "decision": "WATCH",
                "direction": "Long",
                "entry_zone": "100-102",
                "stop_loss": 95,
                "target": 110,
            },
        ],
    )
    csv_path = export_candidate_review_csv(jsonl, output_path=tmp_path / "run_id.csv")
    with csv_path.open(encoding="utf-8") as f:
        row = next(csv.DictReader(f))
    assert "run_timestamp_utc" in CSV_COLUMNS
    assert "run_id" in CSV_COLUMNS
    assert row["run_timestamp_utc"] == "2026-06-05T14:33:25Z"
    assert row["run_id"] == "2026-06-05_pre_market_143325Z"


def test_review_export_writes_latest_and_archive_copy(tmp_path: Path):
    jsonl = tmp_path / "2026-06-08_pre_market.jsonl"
    _write_jsonl(
        jsonl,
        [
            {
                "run_timestamp_utc": "2026-06-08T13:36:41Z",
                "date": "2026-06-08",
                "session": "pre_market",
                "ticker": "NVDA",
                "decision": "WATCH",
                "direction": "Long",
                "entry_zone": "100-102",
                "stop_loss": 95,
                "target": 110,
            },
        ],
    )
    latest = tmp_path / "2026-06-08_pre_market_review.csv"
    csv_path = export_candidate_review_csv(jsonl, output_path=latest)
    assert csv_path == latest.resolve()
    assert latest.is_file()
    archive = tmp_path / "archive" / "2026-06-08_pre_market_133641Z_review.csv"
    assert archive.is_file()
    with archive.open(encoding="utf-8") as f:
        row = next(csv.DictReader(f))
    assert row["run_id"] == "2026-06-08_pre_market_133641Z"


def test_legacy_jsonl_without_run_fields_still_exports(tmp_path: Path):
    jsonl = tmp_path / "legacy_no_run.jsonl"
    _write_jsonl(
        jsonl,
        [
            {
                "date": "2026-05-21",
                "session": "pre_market",
                "ticker": "NVDA",
                "decision": "WATCH",
                "direction": "Long",
                "entry_zone": "100-102",
                "stop_loss": 95,
                "target": 110,
            },
        ],
    )
    csv_path = export_candidate_review_csv(jsonl, output_path=tmp_path / "legacy.csv")
    with csv_path.open(encoding="utf-8") as f:
        row = next(csv.DictReader(f))
    assert row["run_timestamp_utc"] == ""
    assert row["run_id"] == ""
    assert not (tmp_path / "archive").exists()


def test_review_export_backfills_opportunity_from_old_jsonl_row(tmp_path: Path):
    jsonl = tmp_path / "legacy.jsonl"
    _write_jsonl(
        jsonl,
        [
            {
                "run_timestamp_utc": "2026-06-03T12:00:00Z",
                "ticker": "NVDA",
                "decision": "WATCH",
                "direction": "Long",
                "entry_zone": "100-102",
                "stop_loss": 95,
                "target": 120,
                "risk_reward": 2.0,
            },
        ],
    )
    csv_path = export_candidate_review_csv(jsonl, output_path=tmp_path / "legacy.csv")
    with csv_path.open(encoding="utf-8") as f:
        row = next(csv.DictReader(f))
    assert row["opportunity_status"]
    assert row["required_rr"] == "2.5"
    assert row["current_rr"]
    assert row["model_risk_reward"] == "2.0"
    assert row["math_valid"] == "true"


def test_review_export_klac_style_opportunity_backfill(tmp_path: Path):
    jsonl = tmp_path / "klac.jsonl"
    _write_jsonl(
        jsonl,
        [
            {
                "run_timestamp_utc": "2026-06-03T12:00:00Z",
                "ticker": "KLAC",
                "decision": "WATCH",
                "direction": "Long",
                "entry_zone": "2080-2130",
                "stop_loss": 2000,
                "target": 2350,
                "risk_reward": 2.5,
            },
        ],
    )
    csv_path = export_candidate_review_csv(jsonl, output_path=tmp_path / "klac.csv")
    with csv_path.open(encoding="utf-8") as f:
        row = next(csv.DictReader(f))
    assert abs(float(row["current_rr"]) - 1.69) < 0.02
    assert row["opportunity_status"] == OPPORTUNITY_PULLBACK_REQUIRED
    assert row["opportunity_note"]
    assert row["valid_entry_max"] == "2100.0"


def test_review_export_invalid_geometry_backfills_no_actionable_zone(tmp_path: Path):
    jsonl = tmp_path / "invalid.jsonl"
    _write_jsonl(
        jsonl,
        [
            {
                "run_timestamp_utc": "2026-06-03T12:00:00Z",
                "ticker": "AMD",
                "decision": "WATCH",
                "direction": "Short",
                "entry_zone": "100-102",
                "stop_loss": 95,
                "target": 120,
                "risk_reward": 3.0,
            },
        ],
    )
    csv_path = export_candidate_review_csv(jsonl, output_path=tmp_path / "invalid.csv")
    with csv_path.open(encoding="utf-8") as f:
        row = next(csv.DictReader(f))
    assert row["math_valid"] == "false"
    assert row["opportunity_status"] == OPPORTUNITY_NO_ZONE


def test_enrich_records_for_review_export_preserves_latest_run_selection():
    records = select_records_for_review_export(
        [
            {"run_timestamp_utc": "2026-06-03T08:00:00Z", "ticker": "OLD"},
            {
                "run_timestamp_utc": "2026-06-03T12:00:00Z",
                "ticker": "NEW",
                "direction": "Long",
                "entry_zone": "100-102",
                "stop_loss": 95,
                "target": 120,
                "risk_reward": 2.0,
            },
        ]
    )
    enriched = enrich_records_for_review_export(records)
    assert len(enriched) == 1
    assert enriched[0]["ticker"] == "NEW"
    assert enriched[0].get("opportunity_status")


def test_export_csv_includes_opportunity_fields(tmp_path: Path):
    jsonl = tmp_path / "opp.jsonl"
    _write_jsonl(
        jsonl,
        [
            {
                "run_timestamp_utc": "2026-05-29T12:00:00Z",
                "ticker": "KLAC",
                "decision": "WATCH",
                "direction": "Long",
                "entry_zone": "1910.00 - 1935.00",
                "stop_loss": 1810,
                "target": 2050,
                "risk_reward": 2.6,
            },
        ],
    )
    csv_path = export_candidate_review_csv(jsonl, output_path=tmp_path / "opp.csv")
    with csv_path.open(encoding="utf-8") as f:
        row = next(csv.DictReader(f))
    assert row["opportunity_status"] == OPPORTUNITY_PULLBACK_REQUIRED
    assert row["valid_entry_max"] == "1878.57"
    assert row["current_rr"] == "0.92"
    assert "Needs entry" in row["opportunity_note"]


def test_cio_record_finalize_populates_opportunity_fields():
    record = _build_cio_reviewed_record(
        {
            "ticker": "KLAC",
            "decision": "WATCH",
            "direction": "Long",
            "entry_zone": "1910.00 - 1935.00",
            "stop_loss": 1810,
            "target": 2050,
            "risk_reward": 2.6,
        },
        session="pre_market",
        run_timestamp_utc="2026-05-29T12:00:00Z",
        date="2026-05-29",
        summary={},
        rank_score=0.8,
        analysis_rank=1,
    )
    assert record["opportunity_status"] == "PULLBACK_REQUIRED"
    assert record.get("valid_entry_max") is not None
    assert record.get("current_rr") is not None


def test_export_csv_includes_planned_entry_fields(tmp_path: Path):
    jsonl = tmp_path / "planned.jsonl"
    _write_jsonl(
        jsonl,
        [
            {
                "run_timestamp_utc": "2026-06-05T12:00:00Z",
                "ticker": "KLAC",
                "decision": "WATCH",
                "direction": "Long",
                "entry_zone": "2100.00 - 2135.00",
                "stop_loss": 2050,
                "target": 2280,
            },
        ],
    )
    csv_path = export_candidate_review_csv(jsonl, output_path=tmp_path / "planned.csv")
    with csv_path.open(encoding="utf-8") as f:
        row = next(csv.DictReader(f))
    assert "planned_entry_price" in CSV_COLUMNS
    assert row["zone_low"] == "2100.0"
    assert row["zone_mid"] == "2117.5"
    assert row["zone_high"] == "2135.0"
    assert row["conditional_buy_limit"] == "true"
    assert row["planned_entry_price"]
    assert row["planned_entry_rr"] == "2.5"
    assert row["qty_for_1000_notional"]


def test_cio_pass_record_preserves_ta_audit_and_revisit_fields():
    ta_row = {
        "strategy_match": "Momentum",
        "suggested_entry_zone": "2100.00 - 2135.00",
        "suggested_stop_loss": 2050,
        "suggested_target": 2280,
    }
    record = _build_cio_reviewed_record(
        {
            "ticker": "META",
            "decision": "PASS",
            "direction": None,
            "strategy": "No Clean Setup",
        },
        session="pre_market",
        run_timestamp_utc="2026-06-05T12:00:00Z",
        date="2026-06-05",
        summary={},
        rank_score=0.5,
        analysis_rank=3,
        ta_row=ta_row,
    )
    assert record["decision"] == "PASS"
    assert record["ta_math_valid"] is True
    assert record["ta_strategy"] == "Momentum"
    assert record["ta_entry_zone"] == "2100.00 - 2135.00"
    assert record.get("revisit_opportunity_status") == OPPORTUNITY_PULLBACK_REQUIRED
    assert record.get("revisit_planned_entry_price") is not None
    assert record.get("revisit_conditional_buy_limit") is True


def test_export_csv_writes_latest_run_only(tmp_path: Path):
    jsonl = tmp_path / "candidates.jsonl"
    _write_jsonl(
        jsonl,
        [
            {"run_timestamp_utc": "2026-05-29T08:00:00Z", "ticker": "KLAC", "decision": "BUY"},
            {"run_timestamp_utc": "2026-05-29T12:00:00Z", "ticker": "KLAC", "decision": "WATCH"},
        ],
    )
    csv_path = export_candidate_review_csv(jsonl, output_path=tmp_path / "out.csv")
    with csv_path.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    assert rows[0]["ticker"] == "KLAC"
    assert rows[0]["decision"] == "WATCH"
    assert list(rows[0].keys()) == list(CSV_COLUMNS)


if __name__ == "__main__":
    tests = [
        test_latest_run_only_exports_newest_timestamp_group,
        test_stale_buy_ignored_when_latest_row_is_watch,
        test_export_count_matches_latest_run_group,
        test_single_run_behavior_unchanged,
        test_dedupe_keeps_last_row_within_latest_run,
        test_all_runs_exports_every_row,
        test_export_csv_includes_trade_math_audit_columns,
        test_cio_record_finalize_populates_math_audit_fields,
        test_review_export_backfills_opportunity_from_old_jsonl_row,
        test_review_export_klac_style_opportunity_backfill,
        test_review_export_invalid_geometry_backfills_no_actionable_zone,
        test_enrich_records_for_review_export_preserves_latest_run_selection,
        test_export_csv_includes_opportunity_fields,
        test_export_csv_includes_planned_entry_fields,
        test_cio_record_finalize_populates_opportunity_fields,
        test_export_csv_writes_latest_run_only,
        test_cio_pass_record_preserves_ta_audit_and_revisit_fields,
        test_cio_record_includes_run_id_and_timestamp,
        test_export_includes_run_id_backfilled_from_timestamp,
        test_review_export_writes_latest_and_archive_copy,
        test_legacy_jsonl_without_run_fields_still_exports,
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
