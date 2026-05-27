# Phase L — Cost Observability + Model Guardrails

This phase adds two operator-facing controls:
1) **Usage + cost logging** (per call, per run)
2) **Model guardrails** (fail-fast on blocked/unknown models by default)

---

## Usage + cost observability

### What gets logged

- Per call: label, model, input/output tokens, estimated USD cost (if pricing known)
- End of run: a summary line block

Example log shape:

```text
Anthropic usage summary:
  - technical batch 1/3: input=..., output=..., cost_usd=$...
  - sentiment batch 1/3: input=..., output=..., cost_usd=$...
  - cio: input=..., output=..., cost_usd=$...
  Total: input=..., output=..., tokens=..., estimated_cost_usd=$...
```

### Where usage is persisted

When calls occurred, usage is appended to:

```text
data/usage/<YYYY-MM-DD>_<session>_usage.jsonl
```

Note: `--dry-run` skips Discord posts, but still logs usage if the LLM ran.

### Override pricing locally (no code changes)

Set `ANTHROPIC_PRICING_JSON` to override USD per 1M tokens. Example:

```bash
export ANTHROPIC_PRICING_JSON='{
  "claude-sonnet-4-6": {"input_per_million": 3.0, "output_per_million": 15.0},
  "claude-opus-4-7": {"input_per_million": 5.0, "output_per_million": 25.0}
}'
```

---

## Model guardrails

### Policy location

```text
config/model_policy.yaml
```

### Default approved models (policy)

```yaml
approved_models:
  haiku:
    - claude-haiku-4-5-20251001
  sonnet:
    - claude-sonnet-4-6
  opus:
    - claude-opus-4-7
```

### Default behavior

- If a configured model is **blocked** or **unknown**, the run fails fast before any completion calls.

### CLI validation (no completions)

```bash
python -m swingtrade check-models
```

Optional (network call) cache refresh from Anthropic models-list endpoint:

```bash
python -m swingtrade check-models --refresh
```

### Overrides (use sparingly)

Allow unknown models:

```bash
export SWINGTRADE_ALLOW_UNKNOWN_MODELS=1
```

Allow blocked/deprecated models:

```bash
export SWINGTRADE_ALLOW_DEPRECATED_MODELS=1
```

Make cache strict (warn -> error when approved model missing from cache list):

```bash
export SWINGTRADE_STRICT_MODEL_CACHE=1
```

### Local tests (no API calls)

```bash
python tests/test_anthropic_usage.py
python tests/test_model_guard.py
```

