# Dev Handoff (SwingTradeV2)

Use this as a copy/paste handoff template between developers/operators.

---

## Current operating mode

```text
Branch:
Local environment:
Python version:
Install method (venv/pip -e):
```

---

## What to run (most common)

```bash
# pre-market run
python -m swingtrade run --session pre_market

# post-market run
python -m swingtrade run --session post_market
```

---

## Important knobs (CLI)

```text
--max-tickers           Caps trade candidates (excludes Context proxies)
--max-analysis-tickers  Caps post-veto survivors sent to TA/Sentiment
--analysis-batch-size   Caps symbols per TA/Sentiment LLM call (default 15)
--max-cio-tickers       Caps symbols sent to CIO (default 12)
--dry-run               Skips Discord posts (LLM calls still happen if key set)
```

---

## What “success” looks like

```text
- Hard Veto posts with an analysis-cap note when survivors exceed the analysis cap.
- TA and Sentiment posts are coherent (no “score-only” degradation).
- CIO posts trade decisions for up to 12 tickers and does not invent symbols.
- Usage summary logs appear when LLM calls occurred.
```

---

## Smoke tests (no API calls)

```bash
python tests/run_batching_tests.py
python tests/test_cio_normalization.py
python tests/test_anthropic_usage.py
python tests/test_model_guard.py
```

---

## Cursor safety header (paste)

```text
Current branch: jarrid-branch.
Do not merge to main/master.
Do not commit or push without approval.
Do not run pipeline unless explicitly requested.
Do not call Anthropic/API unless explicitly requested.
```

