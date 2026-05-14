# CIO — JSON schema

## Top-level keys

| Key | Type | Purpose |
|-----|------|--------|
| `discord_markdown` | string | Full CIO signal(s) or explicit PASS — markdown. |
| `structured` | object | Must include `decisions` (array). |

## Each element of `structured.decisions`

| Field | Type | Notes |
|-------|------|--------|
| `ticker` | string | e.g. `NVDA`. |
| `direction` | string | `long` or `pass`. |
| `entry` | string | Zone or trigger (text). |
| `stop` | string | Stop level or rule (text). |
| `targets` | array of strings | One or more targets. |
| `timeframe` | string | Hold / swing horizon (text). |
| `position_size_notes` | string | Risk budget / size framing (text). |
| `conviction` | string | `High`, `Medium`, or `Low`. |
| `catalyst` | string | Main driver. |
| `technical_thesis` | string | TA summary in your words. |
| `macro_context` | string | Macro summary in your words. |
| `invalidation` | string | What voids the thesis. |

## Minimal example (shape only)

```json
{
  "discord_markdown": "## Setup\n…\n## Risk\n…",
  "structured": {
    "decisions": [
      {
        "ticker": "NVDA",
        "direction": "long",
        "entry": "…",
        "stop": "…",
        "targets": ["…"],
        "timeframe": "…",
        "position_size_notes": "…",
        "conviction": "Medium",
        "catalyst": "…",
        "technical_thesis": "…",
        "macro_context": "…",
        "invalidation": "…"
      }
    ]
  }
}
```
