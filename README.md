# AI Algo Trading Demo

An AI-assisted strategy builder and market simulator. It turns a short plain-English idea into a restricted strategy JSON representation, validates the rules, and lets users backtest or run an in-memory paper account.

**DEMO MODE is always ON. LIVE ORDERS are disabled.** This application has no exchange credentials or order placement integration. Paper trading only. Not investment advice.

The public Streamlit app has no broker or exchange credential inputs. Its only optional secret is `OPENAI_API_KEY`, used for strategy text parsing; the built-in examples work without it. Demo mode cannot be changed through the UI, environment, or Streamlit secrets.

## Demo

- Demo URL: _not deployed_
- Screenshot: _add after deployment_

## Architecture

```text
Streamlit UI
  ├─ natural language → optional LLM JSON / built-in example parser
  ├─ strict DSL validation (`demo_strategy.py`)
  ├─ public, read-only OHLCV (`demo_data.py`, CCXT Binance spot)
  ├─ fixed safety gates (`demo_safety.py`)
  ├─ next-candle backtest (`demo_backtest.py`)
  └─ in-memory paper account (`demo_paper.py`)
```

The existing command-line bot and its ICT signal modules remain in the repository. The public app imports only the ICT signal for a gated signal display; it never runs the legacy bot loop or any order path.

## Try these strategies

1. Buy when RSI(14) < 30 and exit when RSI(14) > 70.
2. Buy when 20 EMA crosses above 50 EMA and exit when 20 EMA crosses below 50 EMA.
3. Buy after a 2% price dip and exit after a 3% gain.

The built-in parser recognizes these examples without an API key. With `OPENAI_API_KEY` configured, the app can ask the OpenAI chat completion API for JSON-only structured rules. Every response is parsed and validated locally before use. If the API fails, only supported built-in language patterns are parsed locally.

## Restricted strategy DSL

Strategies contain a `side` (`BUY` or `SELL`), non-empty `entry` and `exit` condition lists, and optional risk values. Supported indicators: `price`, `percentage_change`, `RSI`, `SMA`, `EMA`, `MACD`, and `ATR`. Supported operators: `>`, `<`, `>=`, `<=`, `==`, `crosses_above`, `crosses_below`. Indicator periods are limited to 1–200. For crossovers, `compare_to` names another supported moving indicator and period. Invalid JSON, fields, indicators, periods, operators, and risk values are rejected. No generated code is interpreted or executed.

Entry uses BUY/SELL; exit conditions represent an EXIT action. The percentage-change entry measures the change over the configured period. For an open position, a percentage-change exit is measured relative to that position's entry price.

## Existing ICT Strategy

`strategies/ict.py` remains the signal implementation for higher-timeframe bias, liquidity sweeps, displacement, market structure shift, fair value gaps, and order blocks. The demo exposes a gated latest-signal display in the original London and New York UTC windows, with the configured news blackout honored. The legacy multi-strategy command-line runner remains separate and is not invoked by the public UI. The legacy ICT strategy does not currently have a historical signal adapter in the public simulator.

## Backtesting and metrics

The backtester evaluates a finalized candle and fills signals at the next candle open. It charges 0.04% fees per side and 0.01% slippage. If a candle touches stop and target, it assumes the stop was hit first. It sizes positions to the lower of 1% account risk and 1x account notional, applies a 30-minute cooldown, and closes any remaining position at the end of the sample. Metrics include starting/ending capital, return, P&L, maximum marked-to-market drawdown, win rate, trade count, wins/losses, average trade, and profit factor. Historical results are illustrative and do not predict future performance.

## Safety and security

- `DEMO_MODE = True` is a code constant and has no UI toggle or environment override.
- Live orders are disabled by a fixed code constant; the public application contains no order placement integration.
- Every simulation entry point checks demo mode. Risk is capped at 1%, R:R is at least 2.0, leverage/notional is at most 1x, and cooldown is 30 minutes.
- Market candles are validated for missing values, malformed ranges, duplicates, timestamp order, and adequate history before use.
- Market data is fetched from Binance's public spot OHLCV endpoint only. No trading keys are needed.
- API keys are optional and read from `OPENAI_API_KEY`; they are never displayed or logged. `.env` and Streamlit secrets are ignored by Git.
- Natural language output is constrained to JSON and validated. The model has no broker, shell, filesystem, or application-configuration tools.

## Install and run locally

The pinned dependencies were checked locally with Python 3.14.7. Select Python 3.14 in Community Cloud's Advanced settings so deployment uses the same Python version.

```powershell
cd C:\path\to\ict_bot
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
streamlit run app.py
```

The project dependencies are pinned in `requirements.txt`. The API key line in `.env.example` is intentionally empty. To enable optional AI parsing locally, add your own key to `.env`; the three built-in examples work without a key. Never put a real key in a committed file.

Run the safety tests and syntax check from the repository root:

```powershell
python -m unittest discover -s tests -v
python -m compileall -q .
```

## Streamlit Community Cloud

1. Push the reviewed project to your GitHub repository.
2. In Streamlit Community Cloud, create an app from that repository's `main` branch and set the entrypoint file to `app.py`.
3. In Advanced settings, select Python 3.14 to match the local runtime. No secrets are required for the built-in examples.
4. Only if you want optional AI parsing, add `OPENAI_API_KEY` and optionally `OPENAI_MODEL` in the app's Secrets panel. Do not add broker or exchange credentials.
5. Deploy and confirm the safety banner, public candle data, and example backtests.

No Git remote or public app URL is configured yet. The Streamlit entry file is `app.py`. Do not add exchange trading credentials.

## Limitations and disclaimer

This is a prototype for demonstrating strategy serialization and simulation. Public exchange data may be delayed, incomplete, or unavailable. Backtests simplify fills and fees, and paper-trading state lives only in the current Streamlit session. The local parser supports a small set of English patterns; optional AI output can still fail validation. ICT backtesting has not been adapted to this simulator. Nothing here is investment advice, a recommendation, or a promise of profit. **Live trading is disabled.**
