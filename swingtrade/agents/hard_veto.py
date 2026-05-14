from __future__ import annotations

import logging
from typing import Any

import httpx

from swingtrade.integrations.finnhub_client import finnhub_earnings_within_days
from swingtrade.integrations.yfinance_data import last_close_and_adv_usd
from swingtrade.models.agents import AgentResult
from swingtrade.settings import Settings

logger = logging.getLogger(__name__)

# Playbook: long-only liquid names; hard numeric gates here, narrative optional.


def run_hard_veto(
    settings: Settings,
    tickers: list[str],
    watchlist_categories: dict[str, list[str]],
) -> AgentResult:
    http = httpx.Client(timeout=settings.http_timeout_seconds)
    try:
        watchlist_all: set[str] = set()
        for xs in watchlist_categories.values():
            watchlist_all.update(xs)

        rows: list[dict[str, Any]] = []
        for sym in tickers:
            adv_info = last_close_and_adv_usd(sym)
            earnings_hit, earnings_detail = finnhub_earnings_within_days(
                settings, sym, client=http
            )
            last_close = adv_info.get("last_close")
            adv_usd = adv_info.get("adv_usd")
            killed = False
            reasons: list[str] = []
            if isinstance(last_close, (int, float)) and last_close < 5:
                killed = True
                reasons.append("price_below_5")
            if isinstance(adv_usd, (int, float)) and adv_usd < 1_000_000:
                killed = True
                reasons.append("adv_below_1m_usd")
            if earnings_hit:
                killed = True
                reasons.append(f"earnings:{earnings_detail}")
            rows.append(
                {
                    "symbol": sym,
                    "killed": killed,
                    "on_watchlist": sym in watchlist_all,
                    "reasons": reasons,
                    "last_close": last_close,
                    "adv_usd": adv_usd,
                    "earnings": earnings_detail,
                }
            )

        killed_syms = [r["symbol"] for r in rows if r["killed"]]
        survivors = [r["symbol"] for r in rows if not r["killed"]]
        killed_set = set(killed_syms)
        killed_watchlist = sorted(killed_set & watchlist_all)
        lines = [
            "**Hard Veto scan**",
            f"Candidates: {len(tickers)} | Killed: {len(killed_syms)} | "
            f"Downstream (TA / Sentiment / CIO): {len(survivors)}",
        ]
        for r in rows:
            status = "KILL" if r["killed"] else "OK"
            wl = " (watchlist)" if r.get("on_watchlist") else ""
            lines.append(
                f"- `{r['symbol']}` **{status}**{wl} — "
                f"{', '.join(r['reasons']) or 'rules clear'}"
            )
        if killed_watchlist:
            lines.append("")
            lines.append(
                "_Watchlist names that **failed** veto are **not** sent to TA / Sentiment / "
                "CIO this run. They **remain** in `watchlist.yaml` and are **re-checked** every run._"
            )
            shown = killed_watchlist[:35]
            lines.append("**Skipped downstream (still in YAML):** " + ", ".join(f"`{x}`" for x in shown))
            if len(killed_watchlist) > len(shown):
                lines.append(f"_…and {len(killed_watchlist) - len(shown)} more._")
        md = "\n".join(lines)[:19000]
        structured = {
            "vetoes": rows,
            "survivors": survivors,
            "killed": killed_syms,
            "killed_watchlist_still_in_yaml": killed_watchlist,
            "watchlist_categories": watchlist_categories,
        }
        return AgentResult(
            agent_id="hard_veto",
            discord_markdown=md,
            structured=structured,
            model_used="deterministic+finnhub",
        )
    finally:
        http.close()
