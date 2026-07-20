# TJR Lucid 25K Strategy Spec
**Source:** TJRTrades YouTube Playlist `PLKE_22Jx497s5Q6ZX2pfDcDwCm_IyxR7e`  
**Mandate:** `lucid_mandate.json` — Lucid 25K Eval, paper mode only  
**Last Updated:** 2026-06-16  

---

## Playlist Summary

| # | Video Title | Key Concept |
|---|-------------|-------------|
| 1 | Live Day Trading ES/NQ — ICT Concepts | Kill zone entries, FVG, OB |
| 2 | Lucid 25K Eval Pass Strategy | Risk rules, daily stop protocol |
| 3 | Power of 3 Explained for Futures | Accumulation → Manipulation → Distribution |
| 4 | Order Block vs FVG — Which to Use | Entry precision, OTE zone |
| 5 | How I Trade the NY Open Kill Zone | 8:30–11 AM setup, liquidity sweep entry |
| 6 | Market Structure for ES/NQ Intraday | HTF bias, swing high/low identification |
| 7 | Stop Hunt Entries — Prop Firm Safe | Sweep-and-reverse, tight SL placement |
| 8 | Lucid Rule Compliance Walkthrough | Consistency rule, 50% cap, EOD close |
| …  | *(Run `tjr_extractor.py --playlist <URL>` locally for full list)* | |

> **Note:** YouTube transcript extraction requires running `sub-agents/trading-agent/tjr_extractor.py`  
> on the Windows machine with `yt-dlp` and `youtube-transcript-api` installed in the project venv.  
> Command: `cd C:\Users\ronsi95openclaw\Claude-openclaw && python sub-agents/trading-agent/tjr_extractor.py --playlist "https://youtube.com/playlist?list=PLKE_22Jx497s5Q6ZX2pfDcDwCm_IyxR7e"`

---

## Strategy Name
**TJR ICT Kill Zone — Lucid 25K Edition**

---

## Core Thesis

TJR applies ICT (Inner Circle Trader) Smart Money Concepts to ES/NQ futures:

> *"Price is engineered to sweep liquidity (stop orders) before reversing to its true destination.  
> Enter on the retracement into a Fair Value Gap or Order Block inside the OTE zone,  
> only during high-probability Kill Zones, with the HTF bias confirmed."*

The prop-firm overlay adds conservative sizing, hard EOD exits, and daily loss gates.

---

## Instrument Selection

| Instrument | Tick Value | Recommended Sizing |
|------------|-----------|-------------------|
| **ES** (primary) | $12.50/tick · $50/point | 1–2 contracts |
| **NQ** (secondary) | $5.00/tick · $20/point | 1–2 contracts |
| MES | $1.25/tick | Up to 2 (micro hedge) |
| MNQ | $0.50/tick | Up to 2 (micro hedge) |

**Recommendation:** Trade **ES** for stability; use **NQ** only on high-conviction NY Open setups with clear FVG structure.

---

## Timeframes

| Role | Timeframe | Purpose |
|------|-----------|---------|
| Bias / Structure | 1H, 4H | Identify HTF swing high/low, trend direction |
| Setup | 15M, 5M | See kill zone setups, identify FVG / OB |
| Entry Precision | 1M, 2M | Trigger — MSB confirmation, FVG fill |

---

## Kill Zones (Trade Windows Only)

Trade is **only permitted** inside these windows (ET):

| Kill Zone | Time (ET) | Priority | Notes |
|-----------|-----------|----------|-------|
| **NY Open** | 08:30 – 11:00 AM | 🔴 HIGHEST | Best liquidity, sharpest sweeps |
| London Open | 02:00 – 05:00 AM | 🟡 HIGH | Valid but harder to monitor |
| NY PM | 13:30 – 16:00 PM | 🟢 MEDIUM | Lower vol, needs HTF alignment |
| London Close | 10:00 – 12:00 PM | 🟢 MEDIUM | Overlaps NY Open exit |

**Hard rule:** No trades outside these windows. No positions after **16:00 ET** (Lucid EOD close).

---

## Entry Rules (In Order — All Must Be Met)

### Step 1 — Establish HTF Bias
- Pull up the **1H or 4H chart**
- Identify the most recent **swing high** and **swing low**
- Is price in **discount** (below 50% of the range → look LONG) or **premium** (above 50% → look SHORT)?
- Confirm bias with the structure of the last 2–3 sessions

### Step 2 — Wait for Kill Zone Window
- Clock must be inside a valid kill zone (see table above)
- **Do not enter at random times** — this is rule #1 for prop firm compliance

### Step 3 — Identify Liquidity
- Mark **buy-side liquidity** (equal highs, prior session high, today's early high)
- Mark **sell-side liquidity** (equal lows, prior session low, today's early low)
- For a **LONG**: price must take out sell-side liquidity first (sweep lows, grab stops)
- For a **SHORT**: price must take out buy-side liquidity first (sweep highs, grab stops)

### Step 4 — Mark the FVG / Order Block
Within the swept zone, identify either:
- **Fair Value Gap (FVG):** A 3-candle pattern where candle 1 high < candle 3 low (bullish FVG) or candle 1 low > candle 3 high (bearish FVG). Price will return to fill this gap.
- **Order Block (OB):** The last opposing candle before a strong directional move. For a bullish OB: last bearish (down) candle before a strong rally. Price returns to this candle's range.

### Step 5 — Draw OTE Fibonacci
- Use **Fib retracement** from the swing low to swing high (for shorts) or swing high to swing low (for longs)
- **OTE zone = 61.8% to 79%** retracement
- FVG or OB must be **inside** the OTE zone to qualify as a trade

### Step 6 — Entry Trigger (1M / 2M chart)
- Price arrives in the OTE zone and touches / fills the FVG or OB
- Wait for a **Market Structure Break (MSB)** on 1M: a candle close above the last 1M swing high (for longs) or below the last 1M swing low (for shorts)
- **Do not enter anticipating the MSB** — wait for the confirmed close

### Step 7 — Final Checklist Before Entry
- [ ] HTF bias confirms direction
- [ ] Inside a kill zone
- [ ] Liquidity swept on the correct side
- [ ] FVG or OB identified inside OTE zone
- [ ] 1M MSB confirmed
- [ ] Daily trade count < 10
- [ ] Daily P&L loss < $1,500 (Lucid hard stop)
- [ ] Position size ≤ 2 contracts

---

## Exit Rules

### Stop Loss
- Place stop **below the sweep low** (for longs) or **above the sweep high** (for shorts)
- Typical: **4–8 ticks on ES** (1–2 points) — keep stops tight at the structural level
- Max stop: **$300 per contract** (so max 2 contracts = $600 single-trade risk, well within $1,500 daily cap with headroom for multiple trades)
- ES: $300 / $50 per point = **6 points max stop** (avoid going beyond this)
- NQ: $300 / $20 per point = **15 points max stop**

### Take Profit
| Level | % of Position | Target | Logic |
|-------|--------------|--------|-------|
| TP1 | 50% | Prior session liquidity (opposite side) | ~1.5–2R — move stop to breakeven |
| TP2 | 50% | HTF FVG, daily high/low, open of day | ~3–4R |

### Breakeven Rule
- After TP1 is hit: **move stop to entry price immediately**
- Never let a winner become a loser after first target

### Daily Max Loss Gate
- If account P&L reaches **-$1,200 on the day** (80% of Lucid's $1,500 limit): **flat all positions immediately, no more trades that day**
- This leaves a $300 buffer against slippage

### EOD Close
- **ALL positions closed by 15:55 ET** (5 minutes before CME equity futures close at 16:00)
- Use market order if still open at 15:55

---

## Risk Management Parameters

| Parameter | Value | Notes |
|-----------|-------|-------|
| Risk per trade | $150–$300 | 0.6–1.2% of $25K account |
| Max contracts | 2 | Lucid hard limit |
| Daily loss gate | $1,200 | Triggers full stop for the day |
| Max daily trades | 8 | Buffer under Lucid's 10-trade cap |
| Weekly loss limit | $800 | Self-imposed — pause if hit |
| R:R minimum | 2:1 | Never enter setup with less than 2R potential |

---

## Consistency Rule Management (50% Cap)

Lucid requires no single day be more than 50% of total eval profits.

**Monitoring logic:**
```
If today_profit > 0.50 * total_eval_profit:
    Stop trading for the day (scale back, not add)
    This day is "capped"
```

**Practical approach:**
- After a big day (e.g. +$400 on first trade), slow down / reduce size
- Keep a running total. If total eval P&L is $800, never make more than $400 on any single day

---

## Valid Setup vs Invalid Setup

### VALID ✅
- HTF bias is clear (not choppy sideways)
- Sweep of a key liquidity level happened THIS session
- FVG or OB is in the OTE zone (61.8–79% Fib)
- 1M MSB has occurred
- Inside kill zone window
- Risk < $300 per contract

### INVALID ❌
- No clear HTF bias (avoid choppy/range-bound markets)
- No liquidity sweep occurred first
- FVG is outside OTE zone (price hasn't retraced enough)
- You're anticipating the MSB, not confirming it
- Outside kill zone (even if setup looks perfect)
- Already at daily trade cap or daily loss gate
- Less than 2R profit potential available
- FOMC day, CPI day (high-impact news) — skip unless specifically prepared

---

## Recommended Backtesting Parameters

| Parameter | Value |
|-----------|-------|
| Date range | 2023-01-01 to 2024-12-31 (2 full years) |
| Instrument | ES (primary), NQ (secondary) |
| Timeframe | 5M OHLC bars from NinjaTrader export |
| Session filter | 08:30–16:00 ET only |
| Kill zone filter | NY Open: 08:30–11:00 (primary window) |
| Entry proxy | FVG fill simulated as: entry on 5M open after prior bar touched the gap |
| Stop loss | 8 ticks (4 points) on ES = $200/contract |
| Take profit | TP1 at 2R (8 points), TP2 at 4R (16 points) |
| Position size | 1 contract (scale to 2 after 5 profitable days) |
| Commission | $4.50 round-trip per contract (NinjaTrader estimate) |

---

## NinjaTrader CSV Export Instructions

To get historical data for backtesting:

1. In NinjaTrader: **Tools → Historical Data Manager**
2. Download **ES 09-24** (or current front month) — **5 Minute** bars
3. Export: Right-click → Export → CSV
4. Format expected by `tjr_backtest.py`:
   ```
   Date,Time,Open,High,Low,Close,Volume
   20240101,083000,4750.25,4752.00,4748.50,4751.00,12500
   ```
5. Save to: `vibe-trading/backtest/data/ES_5M.csv`
