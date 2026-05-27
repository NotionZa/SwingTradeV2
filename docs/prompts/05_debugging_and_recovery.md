# Debugging + Recovery (SwingTradeV2)

Common operator workflows for diagnosing issues safely.

---

## First triage checklist

```text
1) Confirm branch
2) Confirm git status (avoid staging unrelated data/ or __pycache__)
3) Identify whether failure is:
   - data provider (Finnhub/yfinance)
   - Discord webhook config
   - LLM/Anthropic
   - parsing/normalization
4) Reproduce with smallest safe command (prefer run-agent + --dry-run)
```

---

## Safe reproduction commands

### Single agent dry-run (still calls LLM if key is set)

```bash
python -m swingtrade run-agent technical --session pre_market --dry-run --max-tickers 12
python -m swingtrade run-agent sentiment --session pre_market --dry-run --max-tickers 12
python -m swingtrade run-agent cio --session pre_market --dry-run --max-tickers 12
```

### Validate configured models (no completions)

```bash
python -m swingtrade check-models
```

---

## Common failure modes

### “Stub agent messages”

If `ANTHROPIC_API_KEY` is missing, LLM steps return stubs. Hard veto can still run.

### “No headlines / empty #market-news”

Sentiment depends on `FINNHUB_KEY` (company-news last 7 days, up to 5 articles per ticker).

### CIO returned 0 decisions

Primary suspects:
- CIO payload too large (look for “CIO user message: <n> chars” logs)
- Model returned malformed JSON/shape (normalization fallback should handle many shapes)

Local-only packet sizing check:

```bash
python tests/diagnose_cio_packet.py
```

Local-only normalization tests:

```bash
python tests/test_cio_normalization.py
```

---

## “Report-only” prompt for Cursor

```text
Investigate the failure and report findings only.
Do not change any files.
Do not run the pipeline end-to-end.
Do not call Anthropic/API.
Provide: likely root cause, the smallest fix, and what files would change.
```

