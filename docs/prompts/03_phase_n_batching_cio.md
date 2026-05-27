# Phase N — Batching (TA/Sentiment) + CIO Robustness

This phase is about handling expanded universes (30–45 names) without degrading LLM outputs.

---

## Problem statement

Large symbol lists can degrade structured outputs for Technical and Sentiment. Solution: **internal batching** for those agents, then one final CIO call on a capped pool (default 12).

---

## Operator controls (CLI flags)

### Control universe scanned

```bash
python -m swingtrade run --session pre_market --max-tickers 45
```

### Control analysis + CIO caps

```bash
python -m swingtrade run --session pre_market --max-analysis-tickers 30 --max-cio-tickers 12
```

### Control batch size (TA/Sentiment LLM calls)

Default is 15 symbols per call.

```bash
python -m swingtrade run --session pre_market --analysis-batch-size 15
```

---

## What “batched” means (behavior)

- Hard Veto runs once on the trade-candidate pool.
- Survivors are capped to an **analysis pool** (default 30 unless overridden).
- Technical runs in 1+ batches and merges structured output.
- Sentiment runs in 1+ batches and merges structured output.
- CIO is run once on the top-ranked **CIO pool** (default 12).

---

## CIO payload size gating (local diagnostics)

The CIO user message is built from a compact packet (not full prior agent blobs) and includes:
- `cio_review_tickers` (allowed tickers)
- compact `market_context`
- compact `hard_veto_summary`
- compact per-candidate rows for the CIO pool

Local-only diagnostic script:

```bash
python tests/diagnose_cio_packet.py
```

---

## CIO decision constraint (pool enforcement)

The CIO prompt instructs the model:
- Only return `structured.decisions` for tickers listed in `cio_review_tickers`.
- The runtime normalization also filters out extra/blank tickers.

Relevant prompt excerpt (from `prompts/cio_body.md`):

```text
Return structured.decisions only for tickers listed in cio_review_tickers in the user payload.
Do not introduce symbols that are not in that list.
```

---

## CIO normalization hardening (expected fallback shapes)

Normalization accepts decisions even if the model returns:
- `{"structured": {"decisions": [...]}}` (expected)
- `{"decisions": [...]}` (top-level)
- `{"ticker": "...", "decision": "...", ...}` (single decision object)
- `[{...}, {...}]` (top-level list)

Local tests (no API calls):

```bash
python tests/test_cio_normalization.py
python tests/run_batching_tests.py
```

