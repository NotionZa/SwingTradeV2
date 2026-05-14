# Sentiment — JSON schema

## Top-level keys

| Key | Type | Purpose |
|-----|------|--------|
| `discord_markdown` | string | Must include `## Macro/Tech` and `## Market news` headings. |
| `structured` | object | `macro` + `per_ticker`. |

## `structured.macro`

| Field | Type | Notes |
|-------|------|--------|
| `score_0_10` | int | 0–10 macro / risk tone for risk assets. |
| `catalyst` | string | Short phrase — main macro driver you see in the bundle. |

## `structured.per_ticker`

Object keyed by **ticker** (uppercase), each value:

| Field | Type | Notes |
|-------|------|--------|
| `score_0_10` | int | 0–10 news/social tone for that name. |
| `catalyst` | string | Short phrase — why the score (headline theme, controversy, etc.). |

Include an entry for **each** ticker present in the user bundle when possible.

## Minimal example (shape only)

```json
{
  "discord_markdown": "## Macro/Tech\n…\n\n## Market news\n…",
  "structured": {
    "macro": { "score_0_10": 5, "catalyst": "…" },
    "per_ticker": {
      "NVDA": { "score_0_10": 6, "catalyst": "…" }
    }
  }
}
```
