# AI Algo Trading Demo

A simulation-only trading strategy platform. The shared app lets a user describe a rules-based idea, review and edit a validated specification, inspect a generated Python adapter, backtest it on normalized OHLCV, and paper-trade it in the current Streamlit session. ICT, Quant, and custom DSL strategies are plugins over shared market-data, backtest, risk, and paper-account services.

> **DEMO MODE ONLY. LIVE ORDERS DISABLED.** The public app has no broker credential inputs or live order route. It does not provide investment advice or promise future performance.

## Current platform capabilities

- **Shared strategy registry:** ICT, Quant EMA trend following, Quant RSI mean reversion, and validated custom DSL plugins implement a common signal contract. New plugins can be added without rewriting the UI or simulation engines.
- **Custom strategy workflow:** English, Hindi, and Hinglish rules are interpreted locally for common RSI, EMA/SMA, VWAP, price-vs-average, and previous-candle high/low conditions; Gemini can interpret additional requests when `GEMINI_API_KEY` is configured. The app asks a specific follow-up when an entry is ambiguous or refers to unsupported ICT customization. Validated rules are shown as a confirmation, editable, backtestable, and paper-tradeable. Generated adapter source is deterministic and never executed by the app.
- **ICT:** existing liquidity sweep, displacement, market structure shift, FVG, order block, and higher-timeframe bias logic is preserved. ICT paper entries are evaluated 24 hours a day; configured news blackout, 30-minute cooldown, fixed 1% risk, 2R target, and 1x notional cap still apply. Time is displayed in UTC and IST.
- **Quant:** configurable EMA crossover and RSI mean-reversion plugins use the same normalized candle data and paper/backtest engines.
- **Chart:** TradingView Lightweight Charts 5.2.0 renders OHLCV supplied by this app's validated market-data provider. The chart does not fetch strategy data from TradingView. It needs a browser connection to jsDelivr for the chart library.
- **Market data:** Binance public candles → Coinbase Exchange public candles → bundled historical CSV. The UI names the active source and labels fallback data. Sample data is not live data.
- **Backtest:** finalized-candle signals fill at the next bar open, 0.01% slippage and 0.04% fees per side, 1% risk, minimum 2R, max 1x notional, 30-minute cooldown, stop-first when both SL and TP occur in one candle, and end-of-data close. Metrics include return, P&L, drawdown, wins/losses, profit factor, expectancy, average R, fees, estimated slippage, trade duration, streaks, Sharpe, and Sortino where calculable.
- **Paper trading:** simulated, session-scoped state only. No real orders are sent. Refreshing or ending the session can lose the account and journal state.

## Run locally

Python 3.14 is the pinned deployment target. From PowerShell:

```powershell
cd C:\path\to\ict_bot
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
streamlit run app.py
```

`app.py` is the Streamlit entrypoint. The local parser works without an API key for its documented rule patterns. To enable structured Gemini interpretation, set `GEMINI_API_KEY` in the local ignored `.env` file or in Streamlit Community Cloud's server-side Secrets. `GEMINI_MODEL` is optional. Never place the key in browser code or commit it. `OPENAI_API_KEY` remains as legacy parser compatibility and is used only when no Gemini key is configured.

## Deploy to Streamlit Community Cloud

1. Push the reviewed repository to your GitHub repository.
2. Create the Streamlit app from the repository's `main` branch and set the main file path to `app.py`.
3. Select Python 3.14 in the deployment settings.
4. Add `GEMINI_API_KEY` (and optionally `GEMINI_MODEL`) through the app's server-side Secrets panel if Gemini interpretation is wanted. Do not add exchange or broker credentials.
5. Deploy, then verify the demo safety banner, active market-data source, chart, an ICT signal/backtest, a Quant backtest, custom rule validation, and paper account.

## Safety boundary and implementation limits

- `DEMO_MODE = True` and `LIVE_ORDERS_ENABLED = False` are fixed code constants; the UI, environment, and AI cannot enable live execution.
- All Gemini output is schema-constrained JSON and passed through local DSL validation. Gemini has no app tools, shell, filesystem, broker, or arbitrary network access. Deterministic app code performs validation and execution.
- Supported custom DSL indicators are `price`, `percentage_change`, `RSI`, `SMA`, `EMA`, `MACD`, `ATR`, daily UTC VWAP, and previous-candle high/low. Entry/exit lists are combined with AND; explicit indicator exits are optional because the fixed stop and target can close trades. Ambiguous price-action concepts receive a clarification instead of being silently approximated.
- User-created strategies and paper accounts are held only in the current Streamlit session. There is no authentication, durable database, multi-user data isolation, or cloud journal persistence yet.
- Voice input uses the browser's SpeechRecognition API (English/Indian English or Hindi); typed input remains available where the browser does not provide speech recognition. Optional spoken clarifications use browser speech synthesis. Browser speech availability depends on the user's browser and microphone permissions.
- This repository does not yet include an economic-calendar feed, authenticated TradingView webhook, standalone API backend, persistent portfolio service, or user authentication. Those features are not simulated or represented as working integrations.
- Lightweight Charts attribution is kept visible and its upstream notice is included in `NOTICE`. This app is an independent demo and is not affiliated with TradingView.
- Backtest assumptions are simplified. Results are historical simulations, not a forecast or evidence of profitability. Sharpe/Sortino are unavailable when the sample is insufficient.

## Tests and syntax checks

```powershell
python -m unittest discover -s tests -v
python -m compileall -q .
```

Gemini unit tests mock the SDK call and do not use or reveal a real API key. Market-data provider behavior is mocked in tests except for bundled sample CSV reads.
