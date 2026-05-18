# Market Sentiment — JSON Schema

## Top-level keys

| Key | Type | Purpose |
|-----|------|---------|
| `discord_markdown` | string | Morning briefing letter for Discord #daily-briefing channel |
| `structured` | object | Machine fields for CIO agent — see below |

## `structured` required fields

| Field | Type | Notes |
|-------|------|-------|
| `regime` | string | One of: `bull_trending`, `bear_trending`, `choppy`, `risk_on`, `risk_off`, `high_volatility`, `mean_reversion` |
| `regime_explanation` | string | Plain English explanation of why this regime was chosen and what data led to it |
| `confidence_0_10` | int | 0–10 confidence in regime call. 10 = all signals aligned, 0 = completely unclear |
| `macro_catalysts` | array of objects | List of active or upcoming macro events detected from news headlines - see format below |
| `macro_summary` | string | One paragraph on macro backdrop in plain English - no jargon without explanation |
| `sector_strength_notes` | string | Tech / semis / leaders vs laggards with explanation of what it means for trades |
| `trading_bias` | string | Which strategies are favoured and why |
| `key_levels` | string | Important QQQ levels or ranges worth watching with explanation of why they matter |
| `opex_note` | string | OPEX date and days until it. If within 3 days explain the volatility risk in plain English. If more than 3 days away just note the date. |

## `macro_catalysts` array format
Each catalyst must be an object with:

| Field | Type | Notes |
|-------|------|-------|
| `event` | string | Name of the event e.g. "Fed speaker 2PM ET" |
| `impact` | string | One of: `bullish`, `bearish`, `neutral`, `watch` |
| `explanation` | string | Plain English - what is this event and why does it matter for tech stocks |

## Minimal example

```json
{
  "discord_markdown": "## 🌍 Morning Briefing\n\nGood morning. The market is giving us a clean setup today — here is what you need to know.\n\n**The environment:**\n- **QQQ** is holding above 480 and trending higher — buyers are in control\n- **VIX** (the fear index — measures how nervous traders are) is at 16, which is calm and healthy\n- **SOXX** (semiconductor ETF — tracks chipmakers like NVDA and AMD) is outperforming QQQ, telling us institutions are buying the most growth-sensitive part of tech\n\n**What to watch:**\n- ⚠️ Fed Governor speaks at 2PM ET — if they sound hawkish (favouring higher rates), expect tech to give back some gains this afternoon. Have your stops ready.\n- OPEX in 2 days (June 20th) — options expiry can cause unusual price swings as traders close their contracts. Expect choppier price action.\n\n**Bottom line:**\nThis is a momentum day. Look for breakout setups in semis and AI names. Use full position sizing on high conviction trades but keep stops tight ahead of the Fed speaker and OPEX. Step aside if tone turns hawkish.\n\nTrade well. Manage your risk.",
  "structured": {
    "regime": "bull_trending",
    "regime_explanation": "QQQ is making higher highs and higher lows over the past 5 days. VIX is at 16 and compressing, meaning traders are not fearful. SOXX is outperforming QQQ which tells us the most growth-sensitive part of tech is being bought by institutions. All three signals aligned pointing to a bull trending environment.",
    "confidence_0_10": 8,
    "macro_catalysts": [
      {
        "event": "Fed speaker 2PM ET",
        "impact": "watch",
        "explanation": "When a Federal Reserve official speaks, markets pay close attention. If they sound hawkish (suggesting interest rates will stay high or go higher), tech stocks typically sell off because higher rates make future company earnings worth less in today's money. If they sound dovish (suggesting rate cuts), tech usually rallies."
      }
    ],
    "macro_summary": "The overall market backdrop is constructive for tech swing trades. QQQ is holding key support and VIX is calm at 16. A Fed speaker this afternoon adds some uncertainty. OPEX in 2 days may cause choppier price action. Best approach is to be selective and have stops ready.",
    "sector_strength_notes": "Semiconductors are leading the market via SOXX outperformance. This is a positive signal because semis are the highest-beta part of tech - when institutions are buying semis it means they have high conviction in the tech rally. Mega-cap tech holding steady. Small and mid-cap tech lagging which is normal in an early-stage rally.",
    "trading_bias": "Momentum and breakout strategies favoured. Full position sizing appropriate for high conviction setups. Avoid mean reversion trades today as the trend is your friend. Reduce size or step aside if Fed speaker sounds hawkish.",
    "key_levels": "QQQ support at 480 - this is where buyers have stepped in multiple times recently, a break below here would be a warning sign. Resistance at 492 - the recent high where sellers appeared. A clean break above 492 with volume opens a run toward 500.",
    "opex_note": "Next OPEX is June 20th — 2 days away. Options expiry (the date when options contracts expire) often causes unusual volume and choppy price action as traders rush to close or roll their positions. Keep position sizes slightly smaller than usual and expect wider price swings."
  }
}
```
