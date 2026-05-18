# Market Sentiment — JSON schema

## Top-level keys

| Key | Type | Purpose |
|-----|------|--------|
| `discord_markdown` | string | Concise markdown for Discord (daily / macro context). **Human channel only — not sent to CIO.** |
| `structured` | object | **Complete machine context for CIO** — all fields below are required. |

## `structured` required fields

| Field | Type | Notes |
|-------|------|--------|
| `regime` | string | One of: `bull`, `bear`, `choppy`. |
| `regime_explanation` | string | Why this regime label fits the macro data (2–4 sentences). |
| `confidence_0_10` | int | 0–10 how confident you are in the regime call. |
| `macro_catalysts` | array | **Full list** of near-term macro drivers (see object shape below). Include every material catalyst you infer; do not truncate. |
| `macro_summary` | string | One tight paragraph on macro. |
| `sector_strength_notes` | string | Tech / semis / leaders vs laggards, etc. |
| `trading_bias` | string | Actionable stance for swing longs, e.g. risk-on selective, defensive, wait for pullback. |
| `key_levels` | string | Important levels or ranges worth watching (text). |

### Each element of `macro_catalysts`

| Field | Type | Notes |
|-------|------|--------|
| `event` | string | Catalyst name (e.g. FOMC, CPI, yields, geopolitical). |
| `impact` | string | One of: `bullish`, `bearish`, `neutral`, `mixed`. |
| `explanation` | string | How it affects risk assets / your regime call. |

## Minimal example (shape only)

```json
{
  "discord_markdown": "## Macro snapshot\n- …",
  "structured": {
    "regime": "choppy",
    "regime_explanation": "…",
    "confidence_0_10": 6,
    "macro_catalysts": [
      {
        "event": "Fed path / rates",
        "impact": "mixed",
        "explanation": "…"
      }
    ],
    "macro_summary": "…",
    "sector_strength_notes": "…",
    "trading_bias": "…",
    "key_levels": "…"
  }
}
```
