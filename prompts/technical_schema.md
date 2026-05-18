Technical Analysis — JSON schema

Limits

- discord_markdown must stay under 18,000 characters.

- Output must be valid JSON.

- Every ticker in the input payload should appear in structured.tickers if enough data exists.

- If data is missing or unreliable, include the ticker and mark strategy_match as "No Clean Setup".

- Do not output BUY, WATCH, PASS, or BLOCKED. That belongs to the CIO Agent.

Top-level keys

| Key | Type | Purpose |

|---|---|---|

| discord_markdown | string | Markdown table or grouped bullets for Discord. |

| structured | object | Machine-readable TA output for CIO Agent. |

structured fields

| Field | Type | Purpose |

|---|---|---|

| tickers | object | Map of ticker symbol → full technical analysis object. |

| scores | object | Map ticker → numeric TA score 0–10. Kept for backward compatibility and quick CIO access. |

| notes | string | Cross-cutting TA themes, breadth, risk, sector behaviour, or common setup quality. |

structured.tickers fields

| Field | Type | Required | Notes |

|---|---|---|---|

| ticker | string | Yes | Stock symbol. |

| market_cap_human | string | Yes | Human-readable market cap from yfinance_[quote.market](http://quote.market)_cap_usd, e.g. "$250B", "$1.2T", or "N/A". |

| last_price | number or null | Yes | Latest available price from yfinance_quote.last_price. |

| strategy_match | string | Yes | One of: "Momentum", "Breakout", "Pullback", "No Clean Setup". |

| secondary_context | string or null | No | Additional context, e.g. "Breakout retest", "Testing 50DMA". |

| ta_score | number | Yes | 0–10 technical score. Can include decimals. |

| setup_quality | string | Yes | One of: "A", "B", "C", "No Trade". |

| trend_status | string | Yes | One of: "Bullish", "Neutral", "Bearish", "Broken", "Unknown". |

| momentum_status | string | Yes | One of: "Strong", "Improving", "Neutral", "Weak", "Extended", "Unknown". |

| rsi_comment | string | Yes | Plain-language RSI interpretation. |

| macd_comment | string | Yes | Plain-language MACD interpretation. |

| moving_average_structure | string | Yes | Summary of price vs 20DMA, 50DMA, 200DMA if available. |

| volume_confirmation | string | Yes | Summary of whether volume confirms the move. |

| relative_strength_vs_qqq | string | Yes | One of: "Outperforming", "In line", "Underperforming", "Unknown". |

| key_support | number or null | Yes | Key support level if available. |

| key_resistance | number or null | Yes | Key resistance level if available. |

| suggested_entry_zone | string or null | Yes | Suggested technical entry zone. Null if no clean setup. |

| suggested_stop_loss | number or null | Yes | Technical invalidation/stop level. Null if not available. |

| suggested_target | number or null | Yes | Logical technical target. Null if not available. |

| risk_reward | number or null | Yes | Estimated reward/risk ratio. Null if insufficient structure. |

| technical_risks | array of strings | Yes | Key technical risks. Empty array only if no notable risks. |

| invalidation_condition | string | Yes | What would invalidate the setup technically. |

| summary | string | Yes | Concise technical summary. |

| cio_notes | string | Yes | Specific handoff notes for the CIO Agent. |

Accepted enum values

strategy_match:

- "Momentum"

- "Breakout"

- "Pullback"

- "No Clean Setup"

setup_quality:

- "A"

- "B"

- "C"

- "No Trade"

trend_status:

- "Bullish"

- "Neutral"

- "Bearish"

- "Broken"

- "Unknown"

momentum_status:

- "Strong"

- "Improving"

- "Neutral"

- "Weak"

- "Extended"

- "Unknown"

relative_strength_vs_qqq:

- "Outperforming"

- "In line"

- "Underperforming"

- "Unknown"

Scoring rules

| TA Score | Setup Quality | Meaning |

|---|---|---|

| 8.0–10.0 | A | Strong technical setup with clean structure, clear levels, and acceptable R/R. |

| 6.5–7.9 | B | Promising setup but needs confirmation or has one/two weaknesses. |

| 5.0–6.4 | C | Unclear or low-quality setup. Monitor only. |

| 0.0–4.9 | No Trade | Weak, broken, messy, or insufficient data. |

Risk/reward rules

- A-grade setups require risk_reward >= 2.5.

- If risk_reward is below 2.5, setup_quality cannot be "A".

- If no clean entry, stop, or target exists, risk_reward must be null.

- Do not invent levels if the data does not support them.

Minimal valid example

{

  "discord_markdown": "| Ticker | Mkt Cap | Setup | TA Score | Trend | Momentum | RS vs QQQ | Key Levels | Risk |\n|---|---:|---|---:|---|---|---|---|---|\n| NVDA | $3.1T | Momentum | 8.4 | Bullish | Strong | Outperforming | S: $124.50 / R: $135.00 | Slightly extended |",

  "structured": {

    "scores": {

      "NVDA": 8.4,

      "MSFT": 6.8

    },

    "notes": "Most large-cap tech names remain constructive, but extended names require pullback entries rather than chase entries.",

    "tickers": {

      "NVDA": {

        "ticker": "NVDA",

        "market_cap_human": "$3.1T",

        "last_price": 130.25,

        "strategy_match": "Momentum",

        "secondary_context": "Testing recent resistance",

        "ta_score": 8.4,

        "setup_quality": "A",

        "trend_status": "Bullish",

        "momentum_status": "Strong",

        "rsi_comment": "RSI is in the preferred momentum range and not yet excessively overbought.",

        "macd_comment": "MACD is bullish or improving.",

        "moving_average_structure": "Price is above the 20DMA, 50DMA, and 200DMA.",

        "volume_confirmation": "Volume is above the 20-day average and confirms the move.",

        "relative_strength_vs_qqq": "Outperforming",

        "key_support": 124.5,

        "key_resistance": 135.0,

        "suggested_entry_zone": "128.00 - 131.00",

        "suggested_stop_loss": 124.5,

        "suggested_target": 145.0,

        "risk_reward": 2.7,

        "technical_risks": [

          "Slightly extended from nearest support"

        ],

        "invalidation_condition": "Close below $124.50 support.",

        "summary": "Strong momentum setup with clean trend structure, positive relative strength, and clear invalidation.",

        "cio_notes": "Technically tradeable if hard veto passes and market regime remains supportive."

      },

      "MSFT": {

        "ticker": "MSFT",

        "market_cap_human": "$3.0T",

        "last_price": 420.15,

        "strategy_match": "Pullback",

        "secondary_context": "Testing support near moving averages",

        "ta_score": 6.8,

        "setup_quality": "B",

        "trend_status": "Bullish",

        "momentum_status": "Improving",

        "rsi_comment": "RSI is neutral and may be stabilising.",

        "macd_comment": "MACD is not strongly bullish yet.",

        "moving_average_structure": "Price remains above longer-term moving averages but needs short-term confirmation.",

        "volume_confirmation": "Volume confirmation is not yet strong.",

        "relative_strength_vs_qqq": "In line",

        "key_support": 410.0,

        "key_resistance": 435.0,

        "suggested_entry_zone": "414.00 - 420.00",

        "suggested_stop_loss": 407.5,

        "suggested_target": 435.0,

        "risk_reward": 2.1,

        "technical_risks": [

          "Risk/reward below 2.5",

          "Needs bounce confirmation"

        ],

        "invalidation_condition": "Close below $407.50.",

        "summary": "Constructive pullback but not A-grade yet due to incomplete confirmation and weaker R/R.",

        "cio_notes": "Better suited for WATCH unless confirmation improves."

      }

    }

  }

}

