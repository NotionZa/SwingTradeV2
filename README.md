# SwingTradeV2 — Phase 1

A **scheduled multi-agent pipeline** (Anthropic + market data) that posts to Discord webhooks, plus a **long-running Discord bot** that maintains the watchlist YAML. Strategy, channels, and risk rules live in your separate playbook document if you use one.

---

## What runs where

| Process | Command | Purpose |
|--------|---------|--------|
| **Pipeline** (short-lived) | `python -m swingtrade run …` | Runs agents in order, posts to webhooks, then exits. |
| **Watchlist bot** (always on) | `python -m swingtrade bot` | Slash commands: `/add`, `/remove`, `/liststocks` → updates `config/watchlist.yaml`. |

**Pipeline order:** Market Sentiment → Hard Veto → Technical Analysis → Sentiment → CIO.

**Hard veto:** Symbols that fail price / ADV / earnings gates are **not** sent to TA, Sentiment, or CIO that run. They **stay** in `watchlist.yaml` and are **re-evaluated** on the next pipeline run. Only **survivors** continue downstream.

---

## Requirements

- **Python 3.12+** (including **3.14**). Technical indicators use **pandas only** (no `pandas-ta` / `numba`) so installs stay portable.
- **Environment variables** for APIs and Discord — copy [`.env.example`](.env.example) to `.env` and fill in values. **Never commit `.env`.**

---

## Install

```bash
cd SwingTradeV2          # the inner project folder that contains pyproject.toml
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .
```

Always run commands from the **repository root** (`SwingTradeV2/`) so relative paths resolve for **`config/`** and **`prompts/`**. Optional overrides: `SWINGTRADE_CONFIG_DIR`, `SWINGTRADE_PROMPTS_DIR` (see `.env.example`).

**macOS / Homebrew Python:** use a **venv** (as above); do not `pip install` into the system interpreter (PEP 668 “externally managed”).

### Minimal “does it run?” check

```bash
source .venv/bin/activate
python -m swingtrade run --session pre_market --dry-run --max-tickers 3
```

- **Without** `ANTHROPIC_API_KEY`: upstream LLM steps use short **stubs**; hard veto still runs where data APIs work.
- **With** `ANTHROPIC_API_KEY`: full agent chain runs; `--dry-run` skips Discord webhook POSTs.

Then add webhook URLs to `.env` and run the same command **without** `--dry-run` to post for real. For the watchlist bot, set `DISCORD_BOT_TOKEN` and run `python -m swingtrade bot`.

---

## Commands

```bash
# One-shot pipeline (posts to Discord webhooks unless --dry-run)
python -m swingtrade run --session pre_market
python -m swingtrade run --session post_market

# Dry run: no webhook POSTs (LLMs still run if ANTHROPIC_API_KEY is set)
python -m swingtrade run --session pre_market --dry-run

# Limit how many trade candidates are scanned (after merging universe + watchlist,
# excluding Context-only proxy tickers). Default: 30.
python -m swingtrade run --session post_market --max-tickers 25

# Discord bot (Gateway): watchlist slash commands
python -m swingtrade bot
```

If `pip install -e .` put the script on your `PATH`, you can use `swingtrade run …` and `swingtrade bot` instead of `python -m swingtrade …`.

**Logging:** optional `SWINGTRADE_LOG_LEVEL` (default `INFO`), e.g. `export SWINGTRADE_LOG_LEVEL=DEBUG`.

---

## Scheduling (America/New_York)

Use **cron** or **systemd timers** on the host. Example cron (weekdays, Eastern):

```cron
0 8 * * 1-5 TZ=America/New_York cd /path/to/SwingTradeV2 && . .venv/bin/activate && python -m swingtrade run --session pre_market >> /var/log/swingtrade.log 2>&1
30 16 * * 1-5 TZ=America/New_York cd /path/to/SwingTradeV2 && . .venv/bin/activate && python -m swingtrade run --session post_market >> /var/log/swingtrade.log 2>&1
```

Run the **bot** under **systemd**, **supervisor**, or **Docker** with a restart policy so `/add` and friends stay available.

---

## Configuration

| Area | Location / mechanism |
|------|----------------------|
| **Secrets & URLs** | `.env` (from `.env.example`): Anthropic, Finnhub, NewsAPI, Reddit, Discord webhooks, bot token, optional guild/role allowlists. |
| **Watchlist** | `config/watchlist.yaml` — categories and tickers; bot writes here with safe atomic save. |
| **Universe** | `config/universe.yaml` — broader symbol list merged with the watchlist for scanning. |
| **Agent system prompts** | Split per agent: `prompts/<agent>_body.md` (role, tone, rules) + `prompts/<agent>_schema.md` (JSON shape). Shared envelope: `prompts/_shared_output_contract.md`. Legacy: a single `prompts/<agent>_system.md` still loads if the `_body` / `_schema` pair is missing. |
| **Optional path overrides** | `SWINGTRADE_CONFIG_DIR`, `SWINGTRADE_PROMPTS_DIR` if you keep config or prompts outside the repo copy. |

**Prompt files expected** (for each agent, both `_body` and `_schema`; slugs: `market_sentiment`, `technical`, `sentiment`, `cio`):

- `prompts/_shared_output_contract.md` — optional; prepended to every agent’s schema block.
- `prompts/market_sentiment_body.md` + `prompts/market_sentiment_schema.md`
- `prompts/technical_body.md` + `prompts/technical_schema.md`
- `prompts/sentiment_body.md` + `prompts/sentiment_schema.md`
- `prompts/cio_body.md` + `prompts/cio_schema.md`

`hard_veto` uses deterministic rules + Finnhub (no separate LLM prompt file).

---

## Discord outputs (webhooks)

The pipeline maps agent outputs to the webhook env vars in `.env.example` (e.g. daily briefing pre-market only, trade setups from CIO, etc.). Empty webhook URLs are skipped with a warning.

---

## Security notes

- Treat **webhook URLs** and the **bot token** like passwords; limit who can view Discord integration settings.
- Use **`DISCORD_ALLOWED_GUILD_IDS`** and **`DISCORD_ALLOWED_ROLE_IDS`** if you want only trusted servers/roles to use watchlist commands.
- The app avoids logging full webhook URLs; keep logs off public uploads if they might include request metadata.

---

## Repository layout

```
SwingTradeV2/
├── README.md                 # This file
├── pyproject.toml            # Dependencies and package metadata
├── .env.example              # Template for environment variables
├── config/
│   ├── watchlist.yaml        # Watchlist (bot + manual edits)
│   └── universe.yaml         # Scan universe
├── prompts/
│   ├── _shared_output_contract.md  # JSON envelope (shared)
│   ├── *_body.md / *_schema.md     # Per-agent “meat” vs structure
│   └── …
└── swingtrade/
    ├── __main__.py           # python -m swingtrade
    ├── cli.py                # run | bot
    ├── pipeline.py           # Orchestration + webhook posting
    ├── settings.py           # pydantic-settings from env
    ├── prompt_loader.py      # Loads prompts/*.md
    ├── discord_bot.py        # Slash commands for watchlist
    ├── watchlist_store.py    # Safe YAML I/O
    ├── universe_loader.py    # Universe + merge helpers
    ├── agents/               # One module per agent step
    └── integrations/       # Anthropic, yfinance, Finnhub, NewsAPI, webhooks, pandas-only TA
```

---

## Troubleshooting

- **`FileNotFoundError` for prompts:** Run from repo root or set `SWINGTRADE_PROMPTS_DIR` to the folder that contains each agent’s `*_body.md` + `*_schema.md` pair (or a legacy `*_system.md`).
- **Missing config:** Ensure `config/watchlist.yaml` and `config/universe.yaml` exist (defaults are in the repo).
- **Stub agent messages:** If `ANTHROPIC_API_KEY` is unset, LLM agents return short stubs; hard veto still runs on data where possible.
- **No headlines / empty `#market-news`:** Set **`NEWSAPI_KEY`** or **`NEWS_API_KEY`** in `.env` in the **inner** project folder (run commands from that folder so `.env` loads). NewsAPI often returns HTTP 200 with `status: "error"` in JSON; logs print **`message`**. The client tries **`/v2/top-headlines`** as a fallback. Optional **`NEWSAPI_MACRO_QUERIES`**: pipe-separated broad queries, e.g. `Fed OR rates|tech OR semiconductor`. Per-ticker news runs only for symbols in the current run — raise **`--max-tickers`** (default 30) to cover more names. Test: `SWINGTRADE_LOG_LEVEL=INFO python -c "import logging; logging.basicConfig(level=logging.INFO); from swingtrade.settings import get_settings; from swingtrade.integrations.newsapi_client import newsapi_headlines, news_query_for_equity; get_settings.cache_clear(); s=get_settings(); print('articles', len(newsapi_headlines(s, news_query_for_equity('NVDA'), page_size=3)))"`.
