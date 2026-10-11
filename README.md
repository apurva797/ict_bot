# ARJUNA One-Year Backtest Add-on

This is an add-on package, not an automatic replacement for the existing application. It contains:

- `engine/year_backtest.py`: historical OHLCV loading, validation, caching, indicators, a risk-sized backtest engine, metrics, CSV/JSON export, and CLI.
- `ui/research/year_backtest_screen.py`: optional Streamlit screen.
- `tests/test_year_backtest.py`: basic tests using generated synthetic candles (tests only; not used as market data).

## 1. Copy files into the existing repo

Copy the contents of this folder into the root of `C:\Users\shukl\OneDrive\Desktop\ict_bot`, preserving the `engine`, `ui/research`, and `tests` directories. If those directories already exist, copy only these new files and do not overwrite existing files.

## 2. Ensure dependencies exist

The project's `requirements.txt` should contain these packages (the project already uses several):

```text
ccxt==4.5.78
numpy==2.4.6
pandas==3.0.5
streamlit==1.64.0
```

Use the project's existing Python version and dependency pins where possible. Do not replace the whole requirements file with this subset.

## 3. Run tests in PowerShell

```powershell
cd "C:\Users\shukl\OneDrive\Desktop\ict_bot"
python -m pytest tests/test_year_backtest.py -q
```

## 4. Run one-year historical backtest

```powershell
python -m engine.year_backtest --symbol BTC/USDT --timeframe 5m --days 365 --capital 100000 --strategy all
```

Artifacts are written to `backtest_results/`: strategy comparison, trades, equity curves, and JSON reports.

## 5. Streamlit integration

Import `render_year_backtest_screen` into the existing research screen/navigation and call it from a new navigation entry. The existing `app.py` and navigation are intentionally not rewritten because their current source was not supplied for safe editing.

## Important limitations

- The included strategies are transparent baseline examples (EMA crossover, RSI mean reversion, breakout, and a simple consensus strategy). They are **not** claimed to reproduce Arjuna's native ICT or Quant implementations. Connect the existing strategy signal functions through `run_backtest(..., signal_fn=...)` for faithful strategy results.
- Historical candles from an exchange are subject to its symbol support, API limits, and data retention. Always review `data_quality` and warnings.
- OHLCV cannot reveal the true intrabar order of stop and target. If both levels are touched in a candle, the engine assumes stop loss first.
- This is research/backtesting code, not live execution code. Do not use results as a guarantee of future returns.
