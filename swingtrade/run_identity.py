from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def parse_run_timestamp_utc(value: Any) -> datetime | None:
    """Parse ISO-8601 run timestamp (Z or offset) to UTC datetime."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        else:
            dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def derive_run_id(
    *,
    run_timestamp_utc: Any,
    date: Any = None,
    session: Any = None,
) -> str:
    """Build stable run id: YYYY-MM-DD_session_HHMMSSZ."""
    dt = parse_run_timestamp_utc(run_timestamp_utc)
    if dt is None:
        return ""
    run_date = str(date).strip() if date is not None and str(date).strip() else dt.strftime(
        "%Y-%m-%d"
    )
    sess = str(session).strip() if session is not None else ""
    if not run_date or not sess:
        return ""
    return f"{run_date}_{sess}_{dt.strftime('%H%M%S')}Z"


def enrich_run_identity(record: dict[str, Any]) -> dict[str, Any]:
    """Backfill run_id from run_timestamp_utc + date/session when missing."""
    out = dict(record)
    ts = out.get("run_timestamp_utc")
    if not isinstance(ts, str) or not ts.strip():
        return out
    if out.get("run_id"):
        return out
    run_id = derive_run_id(
        run_timestamp_utc=ts,
        date=out.get("date"),
        session=out.get("session"),
    )
    if run_id:
        out["run_id"] = run_id
    return out
