# SPY / QQQ Options + Day-Trading Bot (Alpaca)

Trades SPY (S&P 500) and QQQ (Nasdaq-100) options on 5-minute technical signals, watches the news
all day, avoids scheduled market-moving events, and manages every exit automatically. It can also
day trade the shares, but that's **off** until the backtest shows it's worth it.

> **Read this first.** This is a tool, not a money printer. Most short-term options traders lose money,
> and nothing here is financial advice. It starts in **paper mode (fake money)**. Run it on paper for at
> least 4–6 weeks and look at `python report.py` before even thinking about real money.

---

## Current strategy: iron condors (two bots)

The bot now sells **iron condors** on SPY and QQQ: it sells an out-of-the-money put and call, and buys a
further-out put and call ("wings") so the most it can lose is capped. It makes money when the price stays
between the two short strikes. **Technical analysis decides when** to open one: only when the chart looks
range-bound (weak trend, RSI near 50, price near its average, Bollinger Bands not expanding).

| | **0DTE bot** (`start_bot.bat`, `config.yaml`) | **30-45 day bot** (`start_bot_swing.bat`, `config_swing.yaml`) |
|---|---|---|
| Expiration | same day | closest to 45 days out (30-45) |
| Short strikes | ~12 delta | ~16 delta |
| Wings | $3 | $5 |
| Minimum credit | 10% of wing width | 20% of wing width |
| Chart used for the range filter | 5-minute (VWAP) | daily (21-day EMA) |
| Take profit | 50% of credit | 50% of credit |
| Stop loss | loss = 1x credit | loss = 1x credit |
| Other exits | price touches a short strike; 2:00 PM CT time exit | close at 21 days to expiration |
| New trades | 8:45-10:45 CT, max 1 per symbol per day | any time 8:45 AM-2:00 PM CT, 1 open per symbol |
| Trade log | `trades.csv` | `trades_swing.csv` |

Both bots size each condor so the **maximum possible loss is 1% of the account**, and both can run at the
same time (each has its own state file, log, and trade journal). `python report.py trades_swing.csv`
shows the 30-45 day bot's results. The old call/put momentum strategy is still available: set
`strategy: type: directional` in `config.yaml`.

**Important:** the backtest/optimize scripts only test the old momentum strategy. Iron condors can't be
backtested accurately with free data, so paper trading is the real test.

### Iron condor research (what the settings are based on)
- **Tastytrade "standard" (45 DTE):** sell ~16-20 delta, take profit at 50% of credit, close at 21 DTE. ([Option Alpha](https://optionalpha.com/videos/tastys-best-practices-iron-condor-automated))
- **Cboe CNDR index** (sells ~20-delta / buys ~5-delta monthly SPX condors, held to expiry): max drawdown 19% vs 51% for the S&P 500 over 35 years, but much lower returns. ([Cboe](https://www.cboe.com/insights/posts/benchmark-indices-series-volatility-management-with-cboes-bfly-and-cndr-indices/))
- **Spintwig (independent backtests, 2007-2023):** SPX condors held to expiry and opened every day did poorly (7-DTE 10/5-delta: Sharpe 0.17, -46% max drawdown); entering only when options were "overpriced" improved this to Sharpe 0.48 / -26%. Both trailed simply holding SPY on total return. ([Spintwig](https://spintwig.com/short-spx-iron-condor-7-dte-s1-signal-options-backtest/))
- **OptionsPilot (vendor backtest, 15,000+ SPX trades 2006-2025):** 16-delta / 45-DTE / 50% profit target was best: 74.6% win rate, Sharpe 0.78, -21% max drawdown, but only ~5-8% a year at 2-3% risk per trade. Treat vendor numbers with caution. ([OptionsPilot](https://optionspilot.app/blog/best-delta-dte-settings-iron-condors-backtest-data))
- **0DTE practitioner results (Theta Profits, 9,100 trades 2021-2026):** 10-15 delta shorts, ~30-point SPX wings, stop on each side about equal to the total credit; only 40% winners but 49 of 57 months profitable. Self-reported. ([Theta Profits](https://www.thetaprofits.com/my-most-profitable-options-trading-strategy-0dte-breakeven-iron-condor/))
- **0DTE risk warning:** frequent small wins and occasional large losses (losses ~2x the size of wins); suggests 50% profit targets, a time exit, and capping daily losses. ([FatTail](https://fattail.ai/0dte-iron-condor/)) A one-year 2022 0DTE backtest (14 delta, 35-pt wings) had an 82.7% win rate but average losses twice the average win and a -28% drawdown. ([OptionsTradingIQ](https://optionstradingiq.com/option-omega/))
- **Technical filters (ADX, RSI, Bollinger Bands):** widely recommended for picking range-bound days, but no credible published study gives numbers for how much they help.


## What it does every minute (9:30–4:00 ET)

1. **News**: pulls headlines for SPY, QQQ, and the mega-caps that drive them (AAPL, MSFT, NVDA, AMZN,
   GOOGL, META, TSLA, AVGO) plus the general market feed. Scores the mood from -1 to +1.
   A "shock" headline (Fed, CPI, jobs, tariffs, war, trading halts...) **pauses new entries for 15 min**.
   Optional: Claude reads the headlines for a smarter score.
2. **Calendar**: no new trades 15 min before → 30 min after CPI / jobs / PCE, and around Fed decisions (`events.yaml`).
3. **Exits first**: checks every open position against its exit rules.
4. **Entries** (9:45–3:00 ET only): for each symbol, scores the setup and buys a call or put if it's strong enough.

### Entry signal (0–7 points, needs 4+)
Needs a **fresh trigger** in the last 3 candles (9/21 EMA cross or a VWAP reclaim/loss), then 1 point each:
price on the right side of VWAP · 9 EMA vs 21 EMA · MACD momentum · RSI in a healthy band (not overbought/oversold)
· ADX ≥ 18 (real trend, not chop) · daily trend agrees · volume above average.
Skipped if price is already stretched > 1.5 ATR from the 21 EMA (no chasing) or the news mood is strongly against it.
Longs are blocked in a daily downtrend and puts in a daily uptrend.

### Contract choice
1–7 days to expiration (no 0DTE by default), delta ~0.45, bid/ask spread under 6%, open interest 500+.
Buys with a limit at the mid price and nudges toward the ask if it doesn't fill.

### Exits (whichever hits first)
+60% take profit · −35% stop · trailing stop (after +30%, exit on a 20% drop from the high) ·
SPY/QQQ hits the chart-based stop or target · 2-hour time stop · everything closed by 3:40 PM ET.

### Risk limits
1% of the account at risk per trade · max 5% of the account in one options trade · max 2 open positions ·
max 4 trades/day · **stops for the day at −2%** · 20-min cooldown after a loss.

All of this is adjustable in `config.yaml` (plain English comments on every line).

---

## Setup (about 20 minutes)

### 1. Alpaca paper account
1. Sign up at **alpaca.markets** (free). Paper trading and options are on by default in paper.
2. In the dashboard, make sure you're on the **Paper** account → **API Keys** → **Generate**. Copy both keys.
3. Set the paper account balance to match what you'd really trade (Paper account → reset → pick the amount), so sizing is realistic.

### 2. Install Python
- **Windows**: python.org → download Python 3.11+ → run the installer, **check "Add Python to PATH"**.
- **Mac**: python.org installer, or `brew install python`.

### 3. Install the bot
Unzip the folder somewhere (e.g. Documents), open a terminal **in that folder**, then:

Windows (Command Prompt):
```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```
Mac:
```
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
chmod +x start_bot.sh
```
Open `.env` in Notepad/TextEdit and paste your two paper keys.

### 4. Check everything works
```
python check_setup.py
```
Every line should say OK. It places no trades.

### 5. Run the backtest (answers "should I day trade the shares too?")
```
python backtest.py            # last 12 months
python backtest.py --days 730 # last 2 years (better)
```
It runs the exact same signals on past 5-min candles and prints win rate, profit factor, drawdown, and a
**VERDICT**. Only if it says **EDGE FOUND** set `trade_shares: true` in `config.yaml` (still in paper).

### 5b. Search for better settings (recommended)
```
python optimize.py
```
Tries 216 versions of the strategy (stop width, target size, signal strictness, trend filter, longs-only,
morning-only). It picks the best 5 using the older 2 years, then checks them on the most recent year,
which they never saw. It only reports **PASSED** if a version holds up on both SPY and QQQ in both periods.
It prints the exact `config.yaml` lines to change. Takes a few minutes the first time (downloads 3 years of data).

### 6. Start the bot
- Windows: double-click `start_bot.bat`
- Mac: `./start_bot.sh`

Start it any time — it sleeps until the market opens (8:30 AM Oklahoma time) and runs until you close it.
Your computer must be **on and awake** during market hours (Windows: Settings → Power → Sleep = Never while plugged in).
If you stop and restart, it picks back up the positions it opened.

### 7. Check results
```
python report.py
```
Win rate, P&L, profit factor, drawdown, by symbol, by day, and why trades closed. Everything is also
in `trades.csv` (opens in Excel) and the day's log in `logs/`.

---

## Optional: Claude reads the news
Get an API key at console.anthropic.com, put it in `.env` as `ANTHROPIC_API_KEY=...`, and set
`news.use_claude: true`. Costs are small (a short request only when new headlines arrive). If it ever
fails, the bot falls back to its keyword scoring automatically. If Anthropic retires the model name in
`claude_model`, swap in a current one from their docs.

## Better data (optional, paid)
The free plan uses IEX stock data (a slice of total volume) and "indicative" option quotes. For more
accurate signals and fills, Alpaca's paid data plan unlocks `stock_feed: sip` and `options_feed: opra`.

## Going live (only after paper results are good)
1. Enable options on your **live** Alpaca account (level 2+ to buy calls/puts) and fund it.
2. Create live API keys and put them in `.env`, plus a line `I_ACCEPT_LIVE_TRADING=YES`.
3. Set `paper: false` in `config.yaml`. Consider halving `risk_per_trade_pct` for the first month.

## Ideas to tune after a few weeks of paper data
- Too many losers? Raise `min_score` to 5.
- Getting stopped then watching it work? Loosen `stop_loss_pct` a bit and lower size.
- Only one side works in the report (calls vs puts)? That's normal in trending years — the daily trend filter helps.
- Don't change more than one setting at a time, or you won't know what helped.

## Troubleshooting
| Problem | Fix |
|---|---|
| `Missing Alpaca keys` | `.env` not filled in, or saved as `.env.txt` (turn on "show file extensions" in Windows). |
| `401 / forbidden` | You're using live keys with `paper: true` (or vice versa). |
| `subscription does not permit querying recent SIP data` | Set `stock_feed: iex` in config.yaml. |
| Option chain FAIL / no quotes | Market closed — option quotes are empty outside 9:30–4:00 ET. Re-run during market hours. |
| "no contract passed filters" | Normal sometimes. Spreads widen at the open/close. |
| Bot stopped when laptop slept | Change sleep settings (Windows) or use `start_bot.sh`, which uses `caffeinate` (Mac). |

## Files
| File | What it is |
|---|---|
| `config.yaml` | All settings |
| `events.yaml` | CPI / jobs / Fed dates — add 2027 dates when published |
| `run_bot.py` | Starts the 0DTE bot (`--config config_swing.yaml` for the 30-45 day bot) |
| `start_bot_swing.bat` / `config_swing.yaml` | The 30-45 day iron condor bot |
| `bot/condor.py` | Iron condor logic (range filter, strike picking, exits, sizing) |
| `check_setup.py` | Tests your setup, no trading |
| `backtest.py` | Tests the strategy on past data |
| `optimize.py` | Searches for better settings, checked on unseen data |
| `report.py` | Your performance |
| `bot/` | The code (strategy, news, options picker, risk, exits, broker) |
| `tests/` | Offline tests: `python -m pip install pytest` then `python -m pytest -q` |
