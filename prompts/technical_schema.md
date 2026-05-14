# Technical Analysis — JSON schema

## Limits

- `discord_markdown` must stay under **18,000** characters.

## Top-level keys

| Key | Type | Purpose |
|-----|------|--------|
| `discord_markdown` | string | Markdown table or bullets for Discord. |
| `structured` | object | Must include `scores` and `notes`. |

## `structured` fields

| Field | Type | Notes |
|-------|------|--------|
| `scores` | object | Map **ticker → integer 0–10** (e.g. `"NVDA": 7`). Every ticker in the user payload should have a score if possible. |
| `notes` | string | Cross-cutting TA themes (breadth, risk, etc.). |

## Minimal example (shape only)

```json
{
  "discord_markdown": "| Ticker | … |\n|--------|---|",
  "structured": {
    "scores": { "NVDA": 7, "MSFT": 6 },
    "notes": "…"
  }
}
```
