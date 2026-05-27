# Pipeline Runbooks (SwingTradeV2)

Operational runbooks for running the pipeline and related CLI commands.

---

## Golden rules

- Run commands from the **repo root** (the folder containing `pyproject.toml` and `config/`).
- `--dry-run` **skips Discord webhook POSTs**, but **LLM calls still happen** if `ANTHROPIC_API_KEY` is set.
- Context-only proxies live under `Context proxies` in `config/watchlist.yaml` and are excluded from the trade-candidate pool.

---

## Quick commands (copy/paste)

### Full pipeline

```bash
python -m swingtrade run --session pre_market
python -m swingtrade run --session post_market
```

### Dry run (no Discord posts)

```bash
python -m swingtrade run --session pre_market --dry-run
```

### Expand/limit universe scanned

`--max-tickers` caps the **trade-candidate** pool after merging `config/universe.yaml` + `config/watchlist.yaml`, excluding `Context proxies`.

```bash
python -m swingtrade run --session post_market --max-tickers 45
```

### Tier caps (analysis vs CIO)

```bash
python -m swingtrade run --session pre_market --max-analysis-tickers 30 --max-cio-tickers 12
```

### Batching (Technical + Sentiment)

When analysis pool is large, Technical and Sentiment are internally batched by `--analysis-batch-size`.

```bash
python -m swingtrade run --session pre_market --analysis-batch-size 15
```

---

## Run a single agent (`run-agent`)

Use `run-agent` when you want **one step** but still want it to post to that agent’s normal webhook(s).

### Windows (recommended)

```powershell
py -3 -m swingtrade run-agent Technical --session pre_market
```

### venv / Unix

```bash
python -m swingtrade run-agent technical --session pre_market
```

Shared flags:

```bash
python -m swingtrade run-agent cio --session pre_market --max-tickers 45 --max-analysis-tickers 30 --max-cio-tickers 12
```

Notes:
- Some agents run upstream steps **silently (no Discord)** to build inputs.
- Agent names are **case-insensitive** (e.g., `Technical`, `technical`, `technical_analysis`).

---

## Watchlist bot

```bash
python -m swingtrade bot
```

---

## Candidate export review CSV

The pipeline can write candidate JSONL under `data/candidates/` (when not `--dry-run`).
Export a review CSV:

```bash
python -m swingtrade review-candidates --file data/candidates/2026-05-21_pre_market.jsonl
```

---

## Model validation (no completions)

Validate configured Anthropic model IDs:

```bash
python -m swingtrade check-models
```

Optional (network call): refresh local cache from Anthropic models-list endpoint:

```bash
python -m swingtrade check-models --refresh
```

