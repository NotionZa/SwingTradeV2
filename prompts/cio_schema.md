# CIO — JSON schema

## Limits

- Output must be valid JSON.

- `discord_markdown` must stay under 18,000 characters.

- Do not use markdown tables in `discord_markdown`.

- Use grouped Discord sections instead.

- Return one top-level JSON object only.

- Top-level keys must be exactly:

  - `discord_markdown`

  - `structured`

---

## Top-level keys

| Key | Type | Purpose |

|---|---|---|

| `discord_markdown` | string | Final CIO decision briefing for Discord. |

| `structured` | object | Machine-readable CIO output. Must include `decisions`, `summary`, and `notes`. |

---

## structured fields

| Field | Type | Required | Notes |

|---|---|---|---|

| `decisions` | array of objects | Yes | One object per ticker decision. |

| `summary` | object | Yes | Session-level CIO summary. |

| `notes` | string | Yes | Cross-cutting CIO notes, risk comments, or limitations. |

---

## structured.summary fields

| Field | Type | Required | Notes |

|---|---|---|---|

| `session` | string | Yes | e.g. `"pre_market"` or `"post_market"`. |

| `market_regime` | string | Yes | Regime from Market Regime Agent. |

| `tech_bias` | string | Yes | CIO interpretation of whether tech longs are favoured. |

| `buy_count` | integer | Yes | Number of BUY decisions. |

| `watch_count` | integer | Yes | Number of WATCH decisions. |

| `pass_count` | integer | Yes | Number of PASS decisions. |

| `blocked_count` | integer | Yes | Number of BLOCKED decisions. |

| `highest_conviction_ticker` | string or null | Yes | Highest conviction ticker, or null if none. |

| `overall_risk_level` | string | Yes | One of: `"Low"`, `"Medium"`, `"High"`, `"Extreme"`. |

| `session_message` | string | Yes | Concise one-sentence CIO summary. |

---

## Each element of structured.decisions

| Field | Type | Required | Notes |

|---|---|---|---|

| `ticker` | string | Yes | Stock symbol, e.g. `"NVDA"`. |

| `decision` | string | Yes | One of: `"BUY"`, `"WATCH"`, `"PASS"`, `"BLOCKED"`. |

| `direction` | string | Yes | For MVP use only `"Long"` or `"None"`. |

| `strategy` | string or null | Yes | `"Momentum"`, `"Breakout"`, `"Pullback"`, `"No Clean Setup"`, or null. |

| `conviction` | string | Yes | One of: `"High"`, `"Medium"`, `"Low"`, `"None"`. |

| `cio_score` | number or null | Yes | 0–10. Use null for BLOCKED if appropriate. |

| `ta_score` | number or null | Yes | TA score from Technical Agent. |

| `sentiment_score` | number or null | Yes | Sentiment score if available. |

| `risk_reward` | number or null | Yes | Reward/risk ratio from Technical Agent, if available. |

| `entry_zone` | string or null | Yes | Entry zone or trigger. Null for PASS/BLOCKED if not relevant. |

| `stop_loss` | number or string or null | Yes | Stop level or stop rule. |

| `target` | number or string or null | Yes | Main target. |

| `targets` | array of strings | Yes | One or more targets as strings. Empty array allowed for PASS/BLOCKED. |

| `position_size_guidance` | string | Yes | Risk guidance, not exact shares. |

| `market_regime` | string | Yes | Regime used by CIO. |

| `technical_thesis` | string | Yes | CIO interpretation of the technical setup. |

| `sentiment_catalyst` | string | Yes | Catalyst/sentiment context. Use `"Neutral / no material catalyst"` if none. |

| `hard_veto_status` | string | Yes | One of: `"PASS"`, `"BLOCKED"`, `"UNKNOWN"`. |

| `hard_veto_reason` | string or null | Yes | Veto reason if blocked, otherwise null. |

| `invalidation_conditions` | array of strings | Yes | What invalidates the setup. |

| `action_required` | string | Yes | What the human should do next. |

| `reason` | string | Yes | Clear reason for the decision. |

| `revisit_condition` | string or null | Yes | What would need to change to revisit a PASS/BLOCKED/WATCH. |

---

## Allowed decision values

Use only:

- `"BUY"`

- `"WATCH"`

- `"PASS"`

- `"BLOCKED"`

---

## Allowed direction values

Use only:

- `"Long"`

- `"None"`

Use `"None"` for PASS and BLOCKED.

---

## Allowed conviction values

Use only:

- `"High"`

- `"Medium"`

- `"Low"`

- `"None"`

Use `"None"` for PASS and BLOCKED.

---

## Allowed strategy values

Use only:

- `"Momentum"`

- `"Breakout"`

- `"Pullback"`

- `"No Clean Setup"`

- null

---

## Allowed hard_veto_status values

Use only:

- `"PASS"`

- `"BLOCKED"`

- `"UNKNOWN"`

---

## Decision rules

### BUY

A BUY decision must include:

- `direction`: `"Long"`

- `conviction`: `"High"` or `"Medium"`

- `cio_score`: normally 8.0 or higher

- `entry_zone`

- `stop_loss`

- `target`

- `risk_reward` of at least 2.5

- `technical_thesis`

- `sentiment_catalyst`

- `invalidation_conditions`

- `action_required`

If any of these are missing, downgrade to WATCH or PASS.

---

### WATCH

A WATCH decision must include:

- what is promising

- what is missing

- exact trigger required for upgrade

- invalidation condition where possible

Use WATCH for:

- promising setups not yet confirmed

- pre-market setups with weak volume

- setups with good structure but incomplete confirmation

- setups with R/R near but not clearly above threshold

---

### PASS

A PASS decision must include:

- clear reason for passing

- what would need to change to revisit

Use PASS for:

- weak technicals

- poor R/R

- no clean levels

- negative sentiment

- unfavourable market regime

- underperformance vs QQQ without strong reversal evidence

---

### BLOCKED

A BLOCKED decision must include:

- `hard_veto_status`: `"BLOCKED"`

- `hard_veto_reason`

- `direction`: `"None"`

- `conviction`: `"None"`

- `position_size_guidance`: `"No position. Ineligible."`

- `revisit_condition`

Use BLOCKED when hard veto fails or survivor status is not confirmed.

---

## Scoring guide

| CIO Score | Typical Decision | Meaning |

|---|---|---|

| 8.0–10.0 | BUY | Strong setup, clean levels, good R/R, supportive regime, no veto. |

| 6.5–7.9 | WATCH | Promising, but needs confirmation or has risk limitations. |

| 0.0–6.4 | PASS | Not actionable. |

| null | BLOCKED | Ineligible due to hard veto or missing critical eligibility. |

---

## Decision caps

- If hard veto failed: decision must be `"BLOCKED"`.

- If hard veto status is unknown: decision cannot be `"BUY"`.

- If R/R is below 2.5: decision cannot be `"BUY"`.

- If setup quality is `"No Trade"`: decision cannot be `"BUY"` or `"WATCH"`.

- If TA score is below 6.0: decision cannot be `"BUY"`.

- If sentiment is strongly negative: decision cannot be `"BUY"`.

- If market regime is `"Risk-Off / Bearish"`: decision should not be `"BUY"` except in exceptional cases.

- If market regime is `"Crisis / High Volatility"`: decision cannot be `"BUY"`.

- If pre-market volume is very weak: decision should usually be `"WATCH"`, not `"BUY"`.

- If no entry, stop, or target exists: decision cannot be `"BUY"`.

- If relative strength vs QQQ is `"Underperforming"`: decision cannot be `"BUY"` unless technical evidence is exceptional.

- If price is extended and entry requires chasing: decision cannot be `"BUY"`.

---

## Discord markdown format

`discord_markdown` should use this structure:

```text

🧠 **SwingTrader — CIO Decision Brief | [Session]**

📌 **Market Context**

Regime:

Tech Bias:

Key Risk:

🟢 **BUY Candidates**

If none, say: No confirmed BUY candidates this session.

🟡 **WATCH Candidates**

Ticker-level summaries.

🔴 **PASS / BLOCKED Summary**

Brief reasons.

📝 **CIO Notes**

Cross-cutting CIO view.

