# Market Sentiment — JSON schema

## Top-level keys

| Key | Type | Purpose |
|-----|------|--------|
| `discord_markdown` | string | Concise markdown for Discord (daily / macro context). |
| `structured` | object | Machine fields — see below. |

## `structured` required fields

| Field | Type | Notes |
|-------|------|--------|
| `regime` | string | One of: `bull`, `bear`, `choppy`. |
| `confidence_0_100` | int | 0–100 how confident you are in the regime call. |
| `macro_summary` | string | One tight paragraph on macro. |
| `sector_strength_notes` | string | Tech / semis / leaders vs laggards, etc. |
| `key_levels` | string | Important levels or ranges worth watching (text). |

## Minimal example (shape only)

```json
{
  "discord_markdown": "## Macro snapshot\n- …",
  "structured": {
    "regime": "choppy",
    "confidence_0_100": 55,
    "macro_summary": "…",
    "sector_strength_notes": "…",
    "key_levels": "…"
  }
}
```
