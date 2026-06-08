from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any, Sequence

from swingtrade.run_identity import derive_run_id, enrich_run_identity

logger = logging.getLogger(__name__)

_ARCHIVE_SUFFIXES = (
    "review_outcomes",
    "opportunities_outcomes",
    "opportunities",
    "review",
)


def archive_suffix_from_stem(stem: str) -> str | None:
    """Map a latest-file stem to its archive suffix (review, opportunities, etc.)."""
    for suffix in _ARCHIVE_SUFFIXES:
        if stem == suffix or stem.endswith(f"_{suffix}"):
            return suffix
    return None


def resolve_run_id_from_records(records: Sequence[dict[str, Any]]) -> str:
    """Resolve run_id from export rows; derive from timestamp when missing."""
    for record in records:
        enriched = enrich_run_identity(dict(record))
        rid = enriched.get("run_id")
        if isinstance(rid, str) and rid.strip():
            return rid.strip()
    for record in records:
        enriched = enrich_run_identity(dict(record))
        rid = derive_run_id(
            run_timestamp_utc=enriched.get("run_timestamp_utc"),
            date=enriched.get("date"),
            session=enriched.get("session"),
        )
        if rid:
            return rid
    return ""


def archive_path_for_run(latest_path: Path, run_id: str) -> Path | None:
    """Build ``{parent}/archive/{run_id}_{suffix}.csv`` for a latest export path."""
    suffix = archive_suffix_from_stem(latest_path.stem)
    if not suffix:
        return None
    return latest_path.parent / "archive" / f"{run_id}_{suffix}.csv"


def write_archive_snapshot(
    latest_path: Path,
    records: Sequence[dict[str, Any]],
) -> Path | None:
    """Copy *latest_path* to a run-specific archive file (overwrite that file only)."""
    latest_path = latest_path.resolve()
    if not latest_path.is_file():
        logger.warning("Skipping archive write; latest file missing: %s", latest_path)
        return None

    run_id = resolve_run_id_from_records(records)
    if not run_id:
        logger.warning(
            "Skipping archive write for %s: no run_id (and cannot derive from records)",
            latest_path,
        )
        return None

    archive_path = archive_path_for_run(latest_path, run_id)
    if archive_path is None:
        logger.warning(
            "Skipping archive write for %s: unrecognized export stem %s",
            latest_path,
            latest_path.stem,
        )
        return None

    archive_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(latest_path, archive_path)
    logger.info("Archive snapshot wrote %s (from %s)", archive_path, latest_path)
    return archive_path
