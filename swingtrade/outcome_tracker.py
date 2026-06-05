from __future__ import annotations

import csv
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from swingtrade.trade_math import _as_float, parse_entry_zone, parse_entry_zone_zones

logger = logging.getLogger(__name__)

FetchOhlcvFn = Callable[[str, date, date], pd.DataFrame]

EXIT_WIN = "WIN"
EXIT_LOSS = "LOSS"
EXIT_NO_ENTRY = "NO_ENTRY"
EXIT_OPEN = "OPEN"
EXIT_INVALID = "INVALID"
EXIT_DATA_ERROR = "DATA_ERROR"

SOURCE_FIELD_KEYS = (
    "date",
    "session",
    "ticker",
    "decision",
    "review_level",
    "analysis_rank",
    "rank_score",
    "direction",
    "strategy",
    "opportunity_status",
    "current_rr",
    "valid_entry_max",
    "planned_entry_price",
    "planned_entry_rr",
    "conditional_buy_limit",
    "entry_zone",
    "stop_loss",
    "target",
    "source_labels",
    "market_regime",
    "tech_bias",
    "overall_risk_level",
)

OUTCOME_FIELD_KEYS = (
    "signal_date",
    "resolved_entry_price",
    "entry_triggered",
    "entry_date",
    "exit_date",
    "exit_type",
    "exit_price",
    "exit_first",
    "pnl_per_share",
    "r_multiple",
    "max_favourable_excursion",
    "max_adverse_excursion",
    "bars_held",
    "horizon_trading_days",
    "outcome_note",
)

OUTPUT_COLUMNS = SOURCE_FIELD_KEYS + OUTCOME_FIELD_KEYS


def default_backtests_dir() -> Path:
    return Path.cwd().resolve() / "data" / "backtests"


def backtest_csv_path_for_input(input_path: Path, backtests_dir: Path | None = None) -> Path:
    stem = input_path.stem
    out_dir = backtests_dir or default_backtests_dir()
    return out_dir / f"{stem}_outcomes.csv"


def _parse_signal_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _is_weekday(d: date) -> bool:
    return d.weekday() < 5


def _trading_days_from(signal_date: date, count: int) -> list[date]:
    """Inclusive window: signal_date plus next (count-1) weekdays."""
    out: list[date] = []
    cursor = signal_date
    while len(out) < count:
        if _is_weekday(cursor):
            out.append(cursor)
        cursor += timedelta(days=1)
        if cursor > signal_date + timedelta(days=count * 3 + 14):
            break
    return out


def resolve_entry_price(row: dict[str, Any]) -> float | None:
    """planned_entry_price > valid_entry_max > zone_mid > entry_zone midpoint."""
    for key in ("planned_entry_price", "valid_entry_max", "zone_mid"):
        val = _as_float(row.get(key))
        if val is not None:
            return round(val, 4)

    zones = parse_entry_zone_zones(row.get("entry_zone"))
    zone_mid = zones.get("zone_mid")
    if zone_mid is not None:
        return round(float(zone_mid), 4)

    low, high = parse_entry_zone(row.get("entry_zone"))
    if low is None or high is None:
        return None
    return round((low + high) / 2, 4) if low != high else round(low, 4)


def _normalize_bars(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.copy()
    if not isinstance(out.index, pd.DatetimeIndex):
        return pd.DataFrame()
    out.index = pd.DatetimeIndex(out.index).tz_localize(None).normalize()
    needed = {"Open", "High", "Low", "Close"}
    if not needed.issubset(out.columns):
        return pd.DataFrame()
    return out.sort_index()


def _bars_for_dates(df: pd.DataFrame, days: list[date]) -> list[tuple[date, pd.Series]]:
    if df.empty:
        return []
    rows: list[tuple[date, pd.Series]] = []
    for d in days:
        ts = pd.Timestamp(d)
        if ts in df.index:
            rows.append((d, df.loc[ts]))
        else:
            # nearest prior bar within 3 calendar days (holiday gaps)
            prior = df.index[df.index <= ts]
            if len(prior) == 0:
                continue
            nearest = prior[-1]
            if (ts - nearest).days <= 3:
                rows.append((d, df.loc[nearest]))
    return rows


def _same_bar_exit_long(
    *,
    low: float,
    high: float,
    stop_loss: float,
    target: float,
) -> tuple[str, float]:
    """Conservative daily-bar tie-break: stop before target when both touched."""
    stop_hit = low <= stop_loss
    target_hit = high >= target
    if stop_hit and target_hit:
        return "STOP", stop_loss
    if stop_hit:
        return "STOP", stop_loss
    if target_hit:
        return "TARGET", target
    return "", 0.0


def simulate_long_outcome(
    bars: pd.DataFrame,
    *,
    signal_date: date,
    entry_price: float,
    stop_loss: float,
    target: float,
    horizon_days: int,
) -> dict[str, Any]:
    """Deterministic long limit-entry replay on daily OHLC bars."""
    window_dates = _trading_days_from(signal_date, horizon_days)
    norm = _normalize_bars(bars)
    window_rows = _bars_for_dates(norm, window_dates)

    base: dict[str, Any] = {
        "signal_date": signal_date.isoformat(),
        "resolved_entry_price": entry_price,
        "horizon_trading_days": horizon_days,
        "entry_triggered": False,
        "entry_date": "",
        "exit_date": "",
        "exit_type": EXIT_NO_ENTRY,
        "exit_price": "",
        "exit_first": "",
        "pnl_per_share": "",
        "r_multiple": "",
        "max_favourable_excursion": "",
        "max_adverse_excursion": "",
        "bars_held": "",
        "outcome_note": "",
    }

    if not window_rows:
        base["exit_type"] = EXIT_DATA_ERROR
        base["outcome_note"] = "No OHLC bars in horizon window"
        return base

    risk = entry_price - stop_loss
    if risk <= 0:
        base["exit_type"] = EXIT_INVALID
        base["outcome_note"] = "Invalid long geometry: stop >= entry"
        return base
    if target <= entry_price:
        base["exit_type"] = EXIT_INVALID
        base["outcome_note"] = "Invalid long geometry: target <= entry"
        return base

    entry_date: date | None = None
    entry_index = -1
    for idx, (d, row) in enumerate(window_rows):
        if float(row["Low"]) <= entry_price:
            entry_date = d
            entry_index = idx
            break

    if entry_date is None:
        base["outcome_note"] = "Entry not touched within horizon"
        return base

    base["entry_triggered"] = True
    base["entry_date"] = entry_date.isoformat()

    mfe = 0.0
    mae = 0.0
    exit_date: date | None = None
    exit_type = EXIT_OPEN
    exit_price: float | None = None
    exit_first = ""
    bars_held = 0

    for idx in range(entry_index, len(window_rows)):
        d, row = window_rows[idx]
        low = float(row["Low"])
        high = float(row["High"])
        mfe = max(mfe, high - entry_price)
        mae = max(mae, entry_price - low)
        bars_held += 1

        first, price = _same_bar_exit_long(
            low=low,
            high=high,
            stop_loss=stop_loss,
            target=target,
        )
        if first == "STOP":
            exit_date = d
            exit_type = EXIT_LOSS
            exit_price = price
            exit_first = "STOP"
            break
        if first == "TARGET":
            exit_date = d
            exit_type = EXIT_WIN
            exit_price = price
            exit_first = "TARGET"
            break

    if exit_date is None:
        last_d, last_row = window_rows[-1]
        exit_date = last_d
        exit_price = float(last_row["Close"])
        exit_type = EXIT_OPEN
        exit_first = "NONE"
        base["outcome_note"] = "Entry triggered; neither stop nor target within horizon"

    pnl = round(exit_price - entry_price, 4)  # type: ignore[operator]
    r_mult = round(pnl / risk, 4)

    base.update(
        {
            "exit_date": exit_date.isoformat() if exit_date else "",
            "exit_type": exit_type,
            "exit_price": exit_price,
            "exit_first": exit_first,
            "pnl_per_share": pnl,
            "r_multiple": r_mult,
            "max_favourable_excursion": round(mfe, 4),
            "max_adverse_excursion": round(mae, 4),
            "bars_held": bars_held,
        }
    )
    return base


def _default_fetch_ohlcv(symbol: str, start: date, end: date) -> pd.DataFrame:
    import yfinance as yf

    try:
        df = yf.Ticker(symbol).history(
            start=start.isoformat(),
            end=(end + timedelta(days=1)).isoformat(),
            interval="1d",
            auto_adjust=True,
        )
    except Exception as exc:
        logger.warning("yfinance fetch failed for %s: %s", symbol, exc)
        return pd.DataFrame()
    return _normalize_bars(df)


def evaluate_signal_row(
    row: dict[str, Any],
    *,
    horizon_days: int,
    fetch_ohlcv: FetchOhlcvFn | None = None,
) -> dict[str, Any]:
    """Evaluate one CSV row; returns merged source + outcome fields."""
    out = {key: row.get(key, "") for key in SOURCE_FIELD_KEYS}
    ticker = str(row.get("ticker") or "").strip().upper()
    signal_date = _parse_signal_date(row.get("date"))

    entry_price = resolve_entry_price(row)
    stop_loss = _as_float(row.get("stop_loss"))
    target = _as_float(row.get("target"))

    if not ticker or signal_date is None:
        out.update(
            {
                "signal_date": signal_date.isoformat() if signal_date else "",
                "resolved_entry_price": entry_price or "",
                "exit_type": EXIT_INVALID,
                "outcome_note": "Missing ticker or signal date",
            }
        )
        return _fill_outcome_defaults(out)

    if entry_price is None or stop_loss is None or target is None:
        out.update(
            {
                "signal_date": signal_date.isoformat(),
                "resolved_entry_price": entry_price or "",
                "exit_type": EXIT_INVALID,
                "outcome_note": "Missing entry/stop/target geometry",
            }
        )
        return _fill_outcome_defaults(out)

    fetch = fetch_ohlcv or _default_fetch_ohlcv
    start = signal_date - timedelta(days=5)
    end = signal_date + timedelta(days=horizon_days * 2 + 10)

    try:
        bars = fetch(ticker, start, end)
    except Exception as exc:
        logger.warning("OHLC fetch error for %s: %s", ticker, exc)
        bars = pd.DataFrame()

    if bars.empty:
        out.update(
            {
                "signal_date": signal_date.isoformat(),
                "resolved_entry_price": entry_price,
                "exit_type": EXIT_DATA_ERROR,
                "outcome_note": "OHLC data unavailable",
            }
        )
        return _fill_outcome_defaults(out)

    outcome = simulate_long_outcome(
        bars,
        signal_date=signal_date,
        entry_price=entry_price,
        stop_loss=stop_loss,
        target=target,
        horizon_days=horizon_days,
    )
    out.update(outcome)
    return out


def _fill_outcome_defaults(row: dict[str, Any]) -> dict[str, Any]:
    for key in OUTCOME_FIELD_KEYS:
        row.setdefault(key, "")
    return row


def load_signal_csv(path: Path) -> list[dict[str, Any]]:
    path = path.resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Signal CSV not found: {path}")
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for line_no, raw in enumerate(reader, start=2):
            if not raw:
                continue
            ticker = str(raw.get("ticker") or "").strip()
            if not ticker:
                logger.warning("Skipping CSV line %s: missing ticker", line_no)
                continue
            rows.append(dict(raw))
    if not rows:
        raise ValueError(f"No signal rows in {path}")
    return rows


@dataclass
class OutcomeSummary:
    total_rows: int = 0
    valid_rows: int = 0
    invalid_rows: int = 0
    data_error_rows: int = 0
    entries_triggered: int = 0
    wins: int = 0
    losses: int = 0
    no_entries: int = 0
    open_trades: int = 0

    @property
    def triggered_trades(self) -> int:
        return self.wins + self.losses + self.open_trades

    @property
    def win_rate(self) -> float | None:
        closed = self.wins + self.losses
        if closed == 0:
            return None
        return round(self.wins / closed * 100.0, 2)

    @property
    def average_r_triggered(self) -> float | None:
        return None  # filled externally


def summarize_outcomes(rows: list[dict[str, Any]]) -> tuple[OutcomeSummary, dict[str, Any]]:
    summary = OutcomeSummary(total_rows=len(rows))
    r_values: list[float] = []
    r_by_decision: dict[str, list[float]] = defaultdict(list)
    r_by_opportunity: dict[str, list[float]] = defaultdict(list)
    r_by_review_level: dict[str, list[float]] = defaultdict(list)

    for row in rows:
        exit_type = str(row.get("exit_type") or "").upper()
        if exit_type == EXIT_INVALID:
            summary.invalid_rows += 1
            continue
        if exit_type == EXIT_DATA_ERROR:
            summary.data_error_rows += 1
            continue

        summary.valid_rows += 1

        if row.get("entry_triggered") in (True, "true", "True", "1"):
            summary.entries_triggered += 1

        if exit_type == EXIT_WIN:
            summary.wins += 1
        elif exit_type == EXIT_LOSS:
            summary.losses += 1
        elif exit_type == EXIT_NO_ENTRY:
            summary.no_entries += 1
        elif exit_type == EXIT_OPEN:
            summary.open_trades += 1

        if exit_type in (EXIT_WIN, EXIT_LOSS, EXIT_OPEN):
            r_val = _as_float(row.get("r_multiple"))
            if r_val is not None:
                r_values.append(r_val)
                dec = str(row.get("decision") or "(blank)")
                opp = str(row.get("opportunity_status") or "(blank)")
                lvl = str(row.get("review_level") or "(blank)")
                r_by_decision[dec].append(r_val)
                r_by_opportunity[opp].append(r_val)
                r_by_review_level[lvl].append(r_val)

    def _avg(vals: list[float]) -> float | None:
        if not vals:
            return None
        return round(sum(vals) / len(vals), 4)

    extras = {
        "average_r_triggered": _avg(r_values),
        "average_r_by_decision": {k: _avg(v) for k, v in sorted(r_by_decision.items())},
        "average_r_by_opportunity_status": {
            k: _avg(v) for k, v in sorted(r_by_opportunity.items())
        },
        "average_r_by_review_level": {k: _avg(v) for k, v in sorted(r_by_review_level.items())},
    }
    return summary, extras


def format_summary_text(summary: OutcomeSummary, extras: dict[str, Any]) -> str:
    lines = [
        "Outcome backtest summary",
        f"  total rows: {summary.total_rows}",
        f"  valid rows: {summary.valid_rows}",
        f"  invalid rows: {summary.invalid_rows}",
        f"  data-error rows: {summary.data_error_rows}",
        f"  entries triggered: {summary.entries_triggered}",
        f"  wins: {summary.wins}",
        f"  losses: {summary.losses}",
        f"  no entries: {summary.no_entries}",
        f"  open: {summary.open_trades}",
    ]
    wr = summary.win_rate
    lines.append(
        f"  win rate (closed triggered): {wr if wr is not None else 'n/a'}%"
    )
    avg_r = extras.get("average_r_triggered")
    lines.append(
        f"  average R (triggered): {avg_r if avg_r is not None else 'n/a'}"
    )
    for label, key in (
        ("decision", "average_r_by_decision"),
        ("opportunity_status", "average_r_by_opportunity_status"),
        ("review_level", "average_r_by_review_level"),
    ):
        bucket = extras.get(key) or {}
        if not bucket:
            lines.append(f"  average R by {label}: n/a")
            continue
        parts = [f"{k}={v}" for k, v in bucket.items()]
        lines.append(f"  average R by {label}: {', '.join(parts)}")
    return "\n".join(lines)


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def export_outcome_csv(rows: list[dict[str, Any]], output_path: Path) -> Path:
    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=OUTPUT_COLUMNS,
            extrasaction="ignore",
        )
        writer.writeheader()
        for row in rows:
            writer.writerow({col: _cell(row.get(col)) for col in OUTPUT_COLUMNS})
    return output_path


def run_outcome_backtest(
    input_csv: Path,
    *,
    horizon_days: int = 10,
    output_path: Path | None = None,
    backtests_dir: Path | None = None,
    fetch_ohlcv: FetchOhlcvFn | None = None,
) -> tuple[Path, list[dict[str, Any]], OutcomeSummary, dict[str, Any]]:
    """Load signals, replay outcomes, write CSV, return results + summary."""
    if horizon_days < 1:
        raise ValueError("horizon_days must be >= 1")

    signals = load_signal_csv(input_csv)
    results: list[dict[str, Any]] = []
    for row in signals:
        try:
            results.append(
                evaluate_signal_row(
                    row,
                    horizon_days=horizon_days,
                    fetch_ohlcv=fetch_ohlcv,
                )
            )
        except Exception as exc:
            logger.warning(
                "Outcome evaluation failed for %s: %s",
                row.get("ticker"),
                exc,
            )
            fallback = {key: row.get(key, "") for key in SOURCE_FIELD_KEYS}
            fallback.update(
                {
                    "exit_type": EXIT_DATA_ERROR,
                    "outcome_note": f"Evaluation error: {exc}",
                }
            )
            results.append(_fill_outcome_defaults(fallback))

    out_path = (
        output_path
        or backtest_csv_path_for_input(input_csv, backtests_dir)
    ).resolve()
    export_outcome_csv(results, out_path)
    summary, extras = summarize_outcomes(results)
    return out_path, results, summary, extras
