"""Research workspace.

Deliberately separated from the trading interface. Nothing here can open,
modify, or close a position: research reads historical candles and the
existing trade journal, and its only outputs are measurements.

The tabs follow the order a trader actually validates a strategy in: run a
backtest, search parameters, test out-of-sample, stress the result, then review
what actually traded and what it implies.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import pandas as pd
import streamlit as st

from ui import components as ui
from ui import marketdata, state
from ui.assistant import render as render_assistant
from ui.journal import render_journal
from ui.research import core

LOGGER = logging.getLogger("ui.research.screens")

TABS = ("Backtest", "Multi-Strategy", "Optimization", "Walk Forward", "Monte Carlo",
        "Robustness", "Trade Journal", "Learnings")


def render() -> None:
    """Render the research workspace with its tab strip."""
    ui.html_block(ui.card(
        f'<div class="ui-row">{ui.status_pill("Research", "accent", dot=False)}'
        f'{ui.pill("Read-only", "info")}</div>'
        '<div class="ui-sub" style="margin-top:.4rem">Research runs on historical '
        "candles and the stored trade journal. It never opens or modifies a "
        "position, and no order is ever sent.</div>",
        variant="flat",
    ))
    st.markdown("")
    from ui.research.multi_strategy import render as render_multi_strategy

    renderers = (_backtest, render_multi_strategy, _optimization, _walk_forward,
                 _monte_carlo, _robustness, _journal, _learnings)
    for panel, renderer in zip(st.tabs(list(TABS)), renderers):
        with panel:
            renderer()


def _store(key: str, payload: dict) -> None:
    """Keep a research result in the session, stamped with its run time."""
    st.session_state[f"research_{key}"] = {
        **payload, "created_at": datetime.now(timezone.utc).isoformat()}


def _stored(key: str) -> dict:
    return st.session_state.get(f"research_{key}") or {}


def _strategy_picker(key_prefix: str, default: str = "quant.trend") -> str:
    """Choose which registered plugin to research."""
    from platform_strategies import strategy_registry
    from ui.strategies import ARJUN_DISPLAY_NAME, ARJUN_KEY

    options = ["quant.trend", "quant.mean_reversion", ARJUN_KEY]
    return st.selectbox(
        "Strategy", options, index=options.index(default),
        format_func=lambda value: (
            f"{ARJUN_DISPLAY_NAME} (ICT Strategy)" if value == ARJUN_KEY
            else strategy_registry.get(value).metadata.name),
        key=f"{key_prefix}_strategy",
    )


def _parameters(strategy_id: str, key_prefix: str) -> dict:
    """Editable parameters for the selected strategy."""
    from ui.strategies import ARJUN_KEY

    if strategy_id == ARJUN_KEY:
        st.caption("ARJUNA's ICT logic is fixed. Only the target multiple is tunable, "
                   "and it is still bounded by the platform's minimum R:R.")
        return {"rr": st.number_input("Target R:R", min_value=1.5, max_value=4.0,
                                      value=2.0, step=0.1,
                                      key=f"{key_prefix}_rr")}
    if strategy_id == "quant.trend":
        first, second = st.columns(2)
        return {
            "fast": first.number_input("Fast EMA", min_value=2, max_value=100,
                                       value=20, key=f"{key_prefix}_fast"),
            "slow": second.number_input("Slow EMA", min_value=3, max_value=200,
                                        value=50, key=f"{key_prefix}_slow"),
        }
    first, second, third = st.columns(3)
    return {
        "period": first.number_input("RSI period", min_value=2, max_value=100,
                                     value=14, key=f"{key_prefix}_period"),
        "oversold": second.number_input("Oversold", min_value=1, max_value=49,
                                        value=30, key=f"{key_prefix}_oversold"),
        "overbought": third.number_input("Overbought", min_value=51, max_value=99,
                                         value=70, key=f"{key_prefix}_overbought"),
    }


def _arjun_signals(frame) -> "pd.Series":
    """Re-derive ARJUNA's signals candle by candle, for a research slice.

    This mirrors what ``run_ict_backtest`` already does, but exposes the signal
    series so a research run can apply its own target multiple through the
    shared ``run_backtest`` rules. The ICT detection itself is untouched: it is
    the existing ``strategies.ict.ict_signal``, run over each candle prefix with
    the existing 24/7 entry gate.
    """
    from demo_safety import ict_entry_gate
    from strategies.ict import ict_signal

    sides: list = []
    for index, stamp in enumerate(frame.index):
        if index < 99 or not ict_entry_gate(stamp)[0]:
            sides.append(None)
            continue
        prefix = [[int(ts.timestamp() * 1000), float(row.open), float(row.high),
                   float(row.low), float(row.close), float(row.volume)]
                  for ts, row in frame.iloc[:index + 1].iterrows()]
        outcome = ict_signal(prefix)
        side = outcome.get("side") if isinstance(outcome, dict) else None
        sides.append("BUY" if side == "LONG" else "SELL" if side == "SHORT" else None)
    return pd.Series(sides, index=frame.index)


def _neutral_rules(rr: float = 2.0) -> dict:
    """Entry/exit rules that always pass, so only ``signal_sides`` decides."""
    return {"side": "BUY",
            "entry": [{"indicator": "price", "operator": ">", "value": 0}],
            "exit": [{"indicator": "price", "operator": "<", "value": 0}],
            "rr": float(rr)}


def _run_backtest(frame, strategy_id: str, parameters: dict) -> core.ConfigResult:
    """Simulate one configuration on a candle slice.

    Delegates to the same ``run_backtest`` the trading screens use, so research
    and paper trading measure identical things. A slice too short to simulate
    is reported, never padded or invented.
    """
    from platform_strategies import strategy_registry
    from ui.strategies import ARJUN_KEY

    if len(frame) < 60:
        return core.ConfigResult(config=core.Config(parameters),
                                 error=f"Only {len(frame)} candles; 60 required.")
    try:
        rr = float(parameters.get("rr", 2.0) or 2.0)
        if strategy_id == ARJUN_KEY:
            signals = _arjun_signals(frame)
        else:
            signals = strategy_registry.get(strategy_id).signal_series(frame, parameters)
        from demo_backtest import run_backtest
        metrics, equity, trades = run_backtest(
            frame, _neutral_rules(rr), starting_capital=10_000.0,
            signal_sides=signals)
        rows = trades.to_dict("records") if trades is not None else []
    except Exception as exc:  # noqa: BLE001 - reported per configuration
        LOGGER.exception("Research backtest failed")
        return core.ConfigResult(config=core.Config(parameters),
                                 error=f"{type(exc).__name__}: {exc}")
    equity_points = []
    if equity is not None and not equity.empty:
        equity_points = [(str(index), float(value))
                         for index, value in equity["equity"].items()]
    return core.ConfigResult(config=core.Config(parameters), metrics=metrics,
                             trades=len(rows), trade_rows=rows,
                             equity_points=equity_points)


def _metric_cards(result: core.ConfigResult, title: str = "Result") -> list[str]:
    """Cards for one configuration's measured performance."""
    net = result.net_pnl
    win_rate = (ui.percent(result.win_rate, signed=False)
                if result.win_rate is not None else "--")
    drawdown = (f'<span class="ui-neg">{ui.percent(result.max_drawdown)}</span>'
                if result.max_drawdown is not None else "--")
    expectancy = ui.money(result.expectancy) if result.expectancy is not None else "--"
    average_r = (ui.ratio(result.average_r, suffix="R")
                 if result.average_r is not None else "--")
    total_return = result.metrics.get("Total return %")
    average_trade = result.metrics.get("Average trade")
    return [
        ui.card(ui.stat(title, f'<span class="{ui.tone_class(net)}">'
                               f"{ui.money(net)}</span>", "Net of fees")),
        ui.card(ui.stat("Total return", ui.percent(total_return)
                        if total_return is not None else "--", "Historical result")),
        ui.card(ui.stat("Trades", ui.esc(str(result.trades)),
                        f'{core.MIN_TRADES} needed to judge')),
        ui.card(ui.stat("Win rate", win_rate, "Closed trades")),
        ui.card(ui.stat("Max drawdown", drawdown, "Peak to trough")),
        ui.card(ui.stat("Profit factor", ui.ratio(result.profit_factor),
                        "Gross win / gross loss")),
        ui.card(ui.stat("Expectancy", expectancy, "Average per trade")),
        ui.card(ui.stat("Average trade", ui.money(average_trade)
                if average_trade is not None else "--", "Net of fees")),
        ui.card(ui.stat("Average R", average_r, "In R multiples")),
    ]


def _unverified_note() -> str:
    """The banner shown above every single backtest result."""
    return ("A single backtest is in-sample only. It has not been tested "
            "out-of-sample, so it is not evidence that this configuration works.")


def _research_frame(symbol: str, timeframe: str, key_prefix: str):
    """Historical candles for a research run, fetched only when one is started.

    Research never auto-fetches on tab open: a sweep replays up to a hundred
    configurations, so the candles should arrive when the user commits to a run,
    not merely because the tab rendered.
    """
    if not st.button("Load research data", key=f"{key_prefix}_load_data",
                     type="primary"):
        ui.html_block(ui.info_state(
            "Research data is not loaded",
            "Loading contacts the public provider chain and downloads up to "
            f"{marketdata.BACKTEST_CANDLES} candles, so it runs when you ask for it.",
        ))
        return None
    return _fetch_research_frame(symbol, timeframe)


def _fetch_research_frame(symbol: str, timeframe: str):
    """Load historical candles for a research run, with a clear failure state."""
    result = marketdata.load(symbol, timeframe, marketdata.BACKTEST_CANDLES)
    if not result.ok:
        ui.html_block(ui.error_state(
            "Market data unavailable",
            f"{result.error} Research needs a validated candle series and cannot "
            f"proceed for {symbol} Â· {timeframe}.",
        ))
        return None
    st.caption(f"Source: {result.source} Â· {len(result.frame)} finalized candles Â· UTC")
    if result.used_fallback:
        ui.html_block(ui.card(ui.info_state(
            "Using backup data source",
            "These results come from bundled historical samples, not a live feed."),
            variant="flat"))
    return result.frame


def _backtest() -> None:
    """Tab 1: one configuration, one historical range, one honest verdict."""
    ui.html_block(ui.section_head("Backtest", "One configuration, historical only"))
    strategy_id = _strategy_picker("bt")
    parameters = _parameters(strategy_id, "bt")
    symbol, timeframe = _market_controls("bt")
    frame = _research_frame(symbol, timeframe, "bt")
    if frame is None:
        return

    result_key = f"backtest_{strategy_id}_{symbol}_{timeframe}"
    if st.button("Run backtest", key="bt_run", type="primary"):
        with st.spinner("Replaying finalized candles..."):
            result = _run_backtest(frame, strategy_id, parameters)
        _store(result_key,
               {"result": result, "strategy": strategy_id, "symbol": symbol,
                "timeframe": timeframe, "parameters": parameters})
    stored = _stored(result_key)
    result = stored.get("result")
    if result is None:
        return
    if result.error:
        ui.html_block(ui.error_state("Backtest failed", result.error))
        return
    ui.html_block(ui.grid(3, *_metric_cards(result, "Backtest P&L")))
    ui.html_block(ui.card(ui.info_state("Not yet validated", _unverified_note()),
                          variant="flat"))
    if result.equity_points:
        ui.html_block(ui.section_head("Equity curve", "Historical simulation"))
        ui.html_block(ui.card(ui.area_chart(
                        [(label[:16], value) for label, value in result.equity_points],
            tone="pos" if result.net_pnl >= 0 else "neg"), variant="flat"))
    if result.trade_rows:
        ui.html_block(ui.section_head("Trade history", f"{result.trades} closed trades"))
        rows = [(ui.esc(str(index)), ui.esc(str(trade.get("side", "--"))),
                 ui.money(trade.get("entry"), signed=False),
                 ui.money(trade.get("exit"), signed=False),
                 f'<span class="{ui.tone_class(trade.get("net_pnl"))}">{ui.money(trade.get("net_pnl"))}</span>')
                for index, trade in enumerate(result.trade_rows, start=1)]
        ui.html_block(ui.table(["#", "Side", "Entry", "Exit", "P&L"], rows,
                               align_right=(2, 3, 4)))


def _market_controls(key_prefix: str) -> tuple[str, str]:
    """Symbol and timeframe pickers shared by the research tabs."""
    first, second = st.columns(2)
    symbol = first.selectbox("Symbol", list(marketdata.watchlist()),
                             key=f"{key_prefix}_symbol")
    timeframe = second.selectbox("Timeframe", list(marketdata.TIMEFRAMES),
                                 key=f"{key_prefix}_timeframe")
    return symbol, timeframe


def _grid_for(strategy_id: str, key_prefix: str) -> dict:
    """The parameter ranges to search, bounded per strategy.

    ``_parameters`` already claims ``{prefix}_fast`` and friends for the
    "single configuration" inputs, so the search grid lives in its own
    namespace. Sharing the prefix would raise a duplicate-widget-key error as
    soon as both sets of inputs render together.
    """
    from ui.strategies import ARJUN_KEY

    prefix = f"{key_prefix}_grid"
    if strategy_id == ARJUN_KEY:
        low, high = st.slider("Target R:R range", 1.5, 4.0, (1.5, 3.0),
                              step=0.25, key=f"{prefix}_rr_range")
        steps = [round(value, 2) for value in
                 [low + index * (high - low) / 2 for index in range(3)]]
        return {"rr": sorted(set(steps))}
    if strategy_id == "quant.trend":
        return {
            "fast": st.multiselect("Fast EMA", [5, 8, 10, 13, 20, 21, 34],
                                   default=[8, 13, 20], key=f"{prefix}_fast"),
            "slow": st.multiselect("Slow EMA", [21, 34, 50, 55, 89, 100],
                                   default=[50, 89], key=f"{prefix}_slow"),
        }
    return {
        "period": st.multiselect("RSI period", [7, 14, 21],
                                 default=[14], key=f"{prefix}_period"),
        "oversold": st.multiselect("Oversold", [20, 25, 30],
                                   default=[30], key=f"{prefix}_oversold"),
        "overbought": st.multiselect("Overbought", [65, 70, 75],
                                     default=[70], key=f"{prefix}_overbought"),
    }


PROGRESS_STEPS = ("Preparing data", "Running configurations", "Calculating metrics",
                  "Running robustness tests", "Generating ranking")


def _optimization() -> None:
    """Tab 2: search a parameter grid, then rank honestly."""
    ui.html_block(ui.section_head(
        "Optimization", "Parameter search over historical candles"))
    st.caption("Searching for the best in-sample result only tells you what fits the "
               "past. Validate a configuration in Walk Forward and Robustness before "
               "treating any of it as usable.")

    strategy_id = _strategy_picker("opt")
    symbol, timeframe = _market_controls("opt")
    grid_values = _grid_for(strategy_id, "opt")
    if any(not values for values in grid_values.values()):
        ui.html_block(ui.error_state("Incomplete search grid",
                                     "Choose at least one value for every parameter."))
        return
    total = 1
    for values in grid_values.values():
        total *= len(values)
    st.caption(f"{total} configuration(s) in this grid.")

    frame = _research_frame(symbol, timeframe, "opt")
    if frame is None:
        return
    if not st.button("Run optimization", key="opt_run", type="primary"):
        return

    progress = st.progress(0.0, text=PROGRESS_STEPS[0])
    outcome = core.sweep(grid_values,
                         lambda config: _run_backtest(frame, strategy_id,
                                                      dict(config.values)))
    progress.progress(0.4, text=PROGRESS_STEPS[1])
    ranked = core.rank(outcome.results)
    progress.progress(0.7, text=PROGRESS_STEPS[2])
    stability = core.parameter_stability(ranked)
    progress.progress(0.85, text=PROGRESS_STEPS[3])
    progress.progress(1.0, text=PROGRESS_STEPS[4])

    _store(f"opt_{strategy_id}_{symbol}_{timeframe}",
           {"sweep": outcome, "ranked": ranked, "stability": stability,
            "strategy": strategy_id, "symbol": symbol, "timeframe": timeframe})
    _render_sweep(ranked, outcome, stability)


def _render_sweep(ranked, outcome, stability: dict) -> None:
    """Ranked configuration table plus an explicit honesty banner."""
    if outcome.truncated:
        ui.html_block(ui.card(ui.error_state(
            "Search truncated",
            f"Only the first {core.MAX_CONFIGURATIONS} of {outcome.total_possible} "
            "configurations ran, to keep the page responsive."), variant="flat"))
    st.success(f"Optimization complete Â· {len(ranked)} configurations evaluated.")
    rows = []
    for index, item in enumerate(ranked, start=1):
        rows.append((
            ui.esc(str(index)),
            ui.esc(item.config.label()),
            (f'<span class="{ui.tone_class(item.net_pnl)}">'
             f'{ui.money(item.net_pnl)}</span>' if not item.error else "--"),
            ui.esc(str(item.trades)),
            ui.percent(item.win_rate, signed=False) if item.win_rate is not None else "--",
            ui.ratio(item.profit_factor),
            f'<span class="ui-neg">{ui.percent(item.max_drawdown)}</span>'
            if item.max_drawdown is not None else "--",
            (ui.pill("Passed", "profit") if item.passed
             else ui.pill(item.error[:28] if item.error else "Too few trades", "warning")),
        ))
    ui.html_block(ui.table(
        ["#", "Configuration", "Net P&L", "Trades", "Win rate", "PF", "Max DD", "Status"],
        rows, align_right=(2, 3, 4, 5, 6)))
    stability_text = ("Stable across the grid" if stability.get("stable")
                      else "Sensitive to exact parameter values")
    ui.html_block(ui.card(
        f'<div class="ui-row">{ui.status_pill("Parameter stability", "accent", dot=False)}'
        f"{ui.pill(stability_text, 'profit' if stability.get('stable') else 'warning')}</div>"
        f'<div class="ui-sub" style="margin-top:.4rem">'
        f"Measured across {stability.get('samples', 0)} configurations that produced "
        f"trades.</div>",
        variant="flat",
    ))
    ui.html_block(ui.card(ui.info_state(
        "This ranking is in-sample",
        "The highest-ranked configuration is not the best configuration. It is the "
        "one that fit this historical range most closely. Run Walk Forward and "
        "Robustness before treating it as viable.",
    ), variant="flat"))


def _walk_forward() -> None:
    """Tab 3: sequential in-sample vs out-of-sample folds."""
    ui.html_block(ui.section_head(
        "Walk Forward", "Does the edge survive outside the data that selected it?"))
    st.caption("Each fold trains on an earlier slice and tests on the slice that "
               "immediately follows it. A configuration that only works in-sample "
               "shows its edge collapsing here.")

    strategy_id = _strategy_picker("wf")
    parameters = _parameters(strategy_id, "wf")
    symbol, timeframe = _market_controls("wf")
    folds = st.slider("Folds", 2, 6, 3, key="wf_folds")
    frame = _research_frame(symbol, timeframe, "wf")
    if frame is None:
        return
    if not st.button("Run walk forward", key="wf_run", type="primary"):
        return

    windows = core.split_walk_forward(frame, folds=folds)
    if not windows:
        ui.html_block(ui.error_state(
            "Not enough data",
            f"{len(frame)} candles cannot be split into {folds} usable folds."))
        return

    with st.spinner("Replaying each fold in sequence..."):
        rows = []
        for number, (train, test) in enumerate(windows, start=1):
            rows.append((number, str(train.index[0])[:10], str(test.index[0])[:10],
                         _run_backtest(train, strategy_id, parameters),
                         _run_backtest(test, strategy_id, parameters)))

    _store(f"wf_{strategy_id}_{symbol}_{timeframe}",
           {"folds": rows, "strategy": strategy_id, "symbol": symbol,
            "timeframe": timeframe, "parameters": parameters})
    _render_walk_forward(rows, parameters)


def _render_walk_forward(rows, parameters: dict) -> None:
    """Per-fold comparison plus an aggregate consistency verdict."""
    body = []
    consistent = 0
    for number, train_from, test_from, in_sample, out_of_sample in rows:
        held = out_of_sample.net_pnl > 0 and not out_of_sample.error
        consistent += int(held)
        body.append((
            ui.esc(str(number)), ui.esc(train_from), ui.esc(test_from),
            ui.esc(str(in_sample.trades)),
            (f'<span class="{ui.tone_class(in_sample.net_pnl)}">'
             f'{ui.money(in_sample.net_pnl)}</span>' if not in_sample.error else "--"),
            ui.esc(str(out_of_sample.trades)),
            (f'<span class="{ui.tone_class(out_of_sample.net_pnl)}">'
             f'{ui.money(out_of_sample.net_pnl)}</span>' if not out_of_sample.error else "--"),
            ui.pill("Held" if held else "Failed", "profit" if held else "loss"),
        ))
    ui.html_block(ui.table(
        ["Fold", "Train from", "Test from", "IS trades", "IS P&L",
         "OOS trades", "OOS P&L", "Verdict"], body, align_right=(3, 4, 5, 6)))

    ratio = consistent / len(rows) if rows else 0.0
    verdict = ("Held out of sample in every fold" if consistent == len(rows)
               else f"Held in {consistent} of {len(rows)} folds")
    ui.html_block(ui.card(
        f'<div class="ui-row">{ui.status_pill("Out-of-sample consistency", "accent", dot=False)}'
        f"{ui.pill(f'{ratio * 100:.0f}%', 'profit' if ratio >= 0.75 else 'warning')}</div>"
        f'<div style="margin-top:.45rem">{ui.esc(verdict)}.</div>'
        f'<div class="ui-sub" style="margin-top:.35rem">Configuration: '
        f'{ui.esc(core.Config(parameters).label())}</div>',
        variant="profit" if ratio >= 0.75 else "loss"))
    if ratio < 0.75:
        ui.html_block(ui.card(ui.info_state(
            "Likely overfitted",
            "This configuration did not hold up outside the data that selected it. "
            "Do not treat it as a working strategy."), variant="flat"))


def _monte_carlo() -> None:
    """Tab 4: resample realised trades to expose ordering risk."""
    ui.html_block(ui.section_head(
        "Monte Carlo", "How much did the result depend on trade ordering?"))
    st.caption("Resamples the sequence of trades that actually happened. This measures "
               "ordering sensitivity, not future performance.")

    trades, label = _pnl_source()
    if trades is None:
        return
    pnls = [state.trade_pnl(trade) for trade in trades]
    if len(pnls) < 2:
        ui.html_block(ui.empty_state(
            "Not enough closed trades",
            "Monte Carlo needs at least two closed trades to resample."))
        return

    runs = st.slider("Simulations", 100, 2000, 500, step=100, key="mc_runs")
    outcome = core.monte_carlo(pnls, runs=runs)
    _store("monte_carlo", {"outcome": outcome, "source": label, "runs": runs})

    cards = [
        ("Median outcome", ui.money(outcome["median"]), f"{runs} resamples"),
        ("Mean outcome", ui.money(outcome["mean"]), "Across resamples"),
        ("5th percentile", ui.money(outcome["p05"]), "Unfavourable case"),
        ("95th percentile", ui.money(outcome["p95"]), "Favourable case"),
        ("Worst drawdown", f'<span class="ui-neg">'
                           f'{ui.money(outcome["worst_drawdown"])}</span>',
         "Across resamples"),
        ("Risk of ruin", ui.percent(outcome["risk_of_ruin"] * 100, signed=False),
         "Sequences finishing at or below zero"),
    ]
    ui.html_block(ui.grid(3, *[ui.card(ui.stat(*item)) for item in cards]))
    ui.html_block(ui.card(ui.info_state(
        "What this does and does not tell you",
        f"Based on {len(pnls)} real closed trades from {label}. It shows how much the "
        "outcome varied with ordering. It cannot tell you whether the strategy has an "
        "edge, and it is not a forecast."), variant="flat"))


def _pnl_source():
    """Choose which realised trades to analyse: the journal or a research run."""
    journal = state.journal()
    choice = st.radio("Analyse", ["Session journal", "Research backtests"],
                      horizontal=True, key="mc_source")
    if choice == "Session journal":
        return (journal, "the session trade journal") if journal else (None, None)

    stored = _latest_backtest_payload()
    if not stored:
        ui.html_block(ui.empty_state(
            "No research results yet",
            "Run a backtest or optimization first, then return here to stress the "
            "resulting trade sequence."))
        return (None, None)
    return stored["trades"], stored["label"]


def _latest_backtest_payload() -> dict | None:
    """The most recent stored sweep, with its best configuration's real trades."""
    keys = sorted((str(name).removeprefix("research_")
                   for name in st.session_state
                   if str(name).startswith("research_opt_")), reverse=True)
    for key in keys:
        ranked = _stored(key).get("ranked") or []
        if ranked and ranked[0].trade_rows:
            best = ranked[0]
            return {"trades": best.trade_rows,
                    "label": f"the optimization run ({best.config.label()})"}
    return None


def _robustness() -> None:
    """Tab 5: grade a configuration instead of assuming it works."""
    ui.html_block(ui.section_head(
        "Robustness", "Does this configuration deserve to be called viable?"))
    st.caption("A configuration is only reported as robust when it clears every "
               "check. A failed check is named, never averaged away.")

    strategy_id = _strategy_picker("rb")
    parameters = _parameters(strategy_id, "rb")
    symbol, timeframe = _market_controls("rb")
    grid_values = _grid_for(strategy_id, "rb")
    if any(not values for values in grid_values.values()):
        ui.html_block(ui.error_state("Incomplete grid",
                                     "Choose at least one value for every parameter."))
        return
    frame = _research_frame(symbol, timeframe, "rb")
    if frame is None:
        return
    if not st.button("Run robustness tests", key="rb_run", type="primary"):
        return

    progress = st.progress(0.0, text="Preparing data")
    outcome = core.sweep(grid_values,
                         lambda config: _run_backtest(frame, strategy_id,
                                                      dict(config.values)))
    progress.progress(0.3, text="Running configurations")
    ranked = core.rank(outcome.results)
    stability = core.parameter_stability(ranked)
    progress.progress(0.6, text="Calculating metrics")

    windows = core.split_walk_forward(frame, folds=3)
    in_sample = ranked[0] if ranked else None
    out_of_sample = (core.ConfigResult(config=core.Config(parameters), trades=0,
                                       error="No out-of-sample window available.")
                     if not windows else
                     _run_backtest(windows[-1][1], strategy_id, parameters))
    progress.progress(0.8, text="Running robustness tests")
    simulation = core.monte_carlo(in_sample.pnls) if in_sample and in_sample.pnls else None
    progress.progress(1.0, text="Generating ranking")

    grade = core.robustness_score(in_sample, out_of_sample, stability, simulation) \
        if in_sample else {"score": 0.0, "verdict": "No results", "components": {},
                           "passes": False, "reason": "The sweep produced no results."}
    _store(f"rb_{strategy_id}_{symbol}_{timeframe}",
           {"in_sample": in_sample, "out_of_sample": out_of_sample,
            "stability": stability, "simulation": simulation, "grade": grade,
            "ranked": ranked, "strategy": strategy_id, "symbol": symbol,
            "timeframe": timeframe, "parameters": parameters})
    _render_grade(in_sample, out_of_sample, stability, simulation, grade)


def _render_grade(in_sample, out_of_sample, stability, simulation, grade) -> None:
    """Show the score, its components, and the reason for any failure."""
    tone = "profit" if grade["passes"] else "loss"
    ui.html_block(ui.card(
        f'<div class="ui-row">{ui.status_pill(grade["verdict"], tone, dot=True)}'
        f'<span class="ui-value ui-value--sm">{ui.ratio(grade["score"], suffix="/100")}</span>'
        "</div>"
        f'<div style="margin-top:.45rem">{ui.esc(grade["reason"])}</div>',
        variant=tone,
    ))
    if not grade["passes"]:
        ui.html_block(ui.card(ui.info_state(
            "Not certified",
            "This configuration did not pass validation and must not be treated as a "
            "working strategy. The reason is stated above."), variant="flat"))

    ui.html_block(ui.section_head("Components", "Each check scores independently"))
    components = ui.table(
        ["Check", "Score"],
        [(name, ui.ratio(value * 100, suffix="%"))
         for name, value in sorted(grade.get("components", {}).items())],
        align_right=(1,),
    )
    ui.html_block(components)

    if in_sample:
        ui.html_block(ui.section_head("Configuration detail", in_sample.config.label()))
        ui.html_block(ui.grid(3, *_metric_cards(in_sample, "In-sample P&L")))
    if out_of_sample is not None and not out_of_sample.error:
        ui.html_block(ui.section_head("Out of sample", "Never used to select parameters"))
        ui.html_block(ui.grid(3, *_metric_cards(out_of_sample, "OOS P&L")))

    ui.html_block(ui.section_head("Parameter stability", "Neighbouring grid points"))
    ui.html_block(ui.card(ui.rows([
        ("Configurations measured", ui.esc(str(stability.get("samples", 0)))),
        ("Median net P&L", ui.money(stability.get("median"))),
        ("Spread across grid", ui.money(stability.get("spread"))),
        ("Verdict", ui.pill("Stable" if stability.get("stable") else "Fragile",
                            "profit" if stability.get("stable") else "warning")),
    ]), variant="flat"))

    if simulation and not simulation.get("insufficient"):
        ui.html_block(ui.section_head("Monte Carlo", "Resampled trade ordering"))
        ui.html_block(ui.card(ui.rows([
            ("Simulations", ui.esc(str(simulation.get("runs", 0)))),
            ("5th percentile", ui.money(simulation.get("p05"))),
            ("95th percentile", ui.money(simulation.get("p95"))),
            ("Risk of ruin", ui.percent((simulation.get("risk_of_ruin") or 0) * 100,
                                        signed=False)),
        ]), variant="flat"))
    else:
        ui.html_block(ui.info_state(
            "Monte Carlo unavailable",
            "Too few trades in this run to resample the sequence."))


def _journal() -> None:
    """Tab 6: every closed trade, with its entry rationale."""
    render_journal(state.journal(), key_prefix="research_journal",
                   title="Trade journal")


def _learnings() -> None:
    """Tab 7: patterns observed in the journal, stated as observations.

    Nothing here infers a cause. Every line reports a measured difference over
    a stated sample, and thin samples say so rather than generalising.
    """
    ui.html_block(ui.section_head(
        "Learnings", "Measured patterns in your own closed trades"))
    st.caption("These are descriptions of what happened in this session, not "
               "explanations of why, and not advice. Treat them as hypotheses to "
               "test, not conclusions.")

    trades = state.journal()
    if not trades:
        ui.html_block(ui.empty_state(
            "No learnings yet",
            "Patterns appear once the paper journal has closed trades to compare.",
        ))
        return

    observations = _observe(trades)
    if not observations:
        ui.html_block(ui.empty_state(
            "Not enough data for patterns",
            f"{len(trades)} closed trade(s) is too few to compare anything. "
            "Ten or more is a minimum start.",
        ))
        return
    for title, body in observations:
        ui.html_block(ui.card(
            f'<div style="font-weight:640">{ui.esc(title)}</div>'
            f'<div class="ui-sub" style="margin-top:.25rem">{body}</div>',
            interactive=True,
        ))

    render_assistant()


def _observe(trades: list[dict]) -> list[tuple[str, str]]:
    """Build the observation list from real trade groupings."""
    notes: list[tuple[str, str]] = []

    by_session: dict[str, list[float]] = {}
    for trade in trades:
        by_session.setdefault(state.trade_session(trade), []).append(
            state.trade_pnl(trade))
    ranked = sorted(by_session.items(), key=lambda item: sum(item[1]), reverse=True)
    if len(ranked) >= 2 and len(ranked[0][1]) >= 3:
        best_name, best_pnls = ranked[0]
        worst_name, worst_pnls = ranked[-1]
        notes.append((
            f"{best_name} performed best by total P&L",
            f"{best_name} booked {ui.money(sum(best_pnls))} across "
            f"{len(best_pnls)} trades; {worst_name} booked "
            f"{ui.money(sum(worst_pnls))} across {len(worst_pnls)}.",
        ))

    by_day = state.daily_pnl(trades)
    if len(by_day) >= 2:
        best_day, best_value = max(by_day, key=lambda item: item[1])
        worst_day, worst_value = min(by_day, key=lambda item: item[1])
        notes.append((
            "Best and worst day",
            f"Best was {best_day} at {ui.money(best_value)}; worst was "
            f"{worst_day} at {ui.money(worst_value)}, across {len(by_day)} "
            "trading days.",
        ))

    exits: dict[str, list[float]] = {}
    for trade in trades:
        exits.setdefault(str(trade.get("exit_reason") or "Unknown"), []).append(
            state.trade_pnl(trade))
    if len(exits) >= 2:
        parts = " Â· ".join(f"{name}: {ui.money(sum(pnls))} over {len(pnls)}"
                           for name, pnls in sorted(exits.items()))
        notes.append(("How trades ended", parts))

    longs = [state.trade_pnl(trade) for trade in trades
             if state.trade_direction(trade) == "LONG"]
    shorts = [state.trade_pnl(trade) for trade in trades
              if state.trade_direction(trade) == "SHORT"]
    if longs and shorts:
        notes.append((
            "Long versus short",
            f"Longs booked {ui.money(sum(longs))} over {len(longs)} trades; "
            f"shorts booked {ui.money(sum(shorts))} over {len(shorts)}.",
        ))

    losses = [trade for trade in trades if state.trade_pnl(trade) < 0]
    if len(losses) >= 3:
        worst = sorted(losses, key=state.trade_pnl)[:3]
        parts = ", ".join(f"{state.trade_symbol(trade)} "
                          f"{ui.money(state.trade_pnl(trade))}" for trade in worst)
        notes.append(("Three largest realised losses", parts))
    return notes
