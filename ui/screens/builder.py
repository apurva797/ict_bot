"""AI strategy builder.

Preserves the existing natural-language strategy workflow: describe an idea,
review the validated specification, edit it, then backtest or paper-trade it.
Interpretation, validation, and code generation all stay in ``demo_strategy``;
this module only sequences the steps and presents their output.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import streamlit as st

from demo_backtest import run_backtest
from demo_data import MarketDataError
from demo_paper import advance_paper_account
from demo_safety import SafetyError
from demo_strategy import (EXAMPLES, StrategyError, generate_strategy_code,
                           interpret_strategy, parse_strategy_json,
                           summarize_strategy)
from ui import components as ui
from ui import marketdata, state
from ui.screens.trade import render_account_state
from voice_strategy import render_voice_strategy_controls

LOGGER = logging.getLogger("ui.screens.builder")

# The one button the existing end-to-end tests look for by label.
EXAMPLE_BUTTONS = ("Example 1", "Example 2", "Example 3")


def render(symbol: str, timeframe: str, starting_capital: float) -> None:
    """Render the AI strategy builder workspace."""
    ui.html_block(ui.section_head(
        "AI Strategy", "Describe an idea in English, Hindi, or Hinglish"))
    st.caption(
        "Rules are interpreted locally into the platform's restricted strategy "
        "language, then validated before they can be backtested or paper-traded. "
        "Voice input works in browsers that support speech recognition."
    )
    _describe()
    st.divider()
    _review(symbol, timeframe, starting_capital)


def _describe() -> None:
    """Step one: capture the strategy description, by text or voice."""
    if "strategy_text" not in st.session_state:
        st.session_state.strategy_text = ""
    selected_example = st.session_state.pop("_selected_example", None)

    examples = st.columns(3, gap="small")
    for index, (column, example) in enumerate(zip(examples, EXAMPLES[:3])):
        column.button(f"Example {index + 1}", key=f"builder_example_{index}",
                      width="stretch", on_click=_select_example, args=(example,))

    voice_language = st.selectbox(
        "Voice language", ["Hinglish / Indian English", "Hindi", "English"],
        key="strategy_voice_language",
    )
    voice_reply = st.checkbox("Speak clarification and confirmation",
                              key="strategy_voice_reply")
    prior = st.session_state.get("strategy_interpretation")
    voice_prompt = ""
    if voice_reply and prior:
        voice_prompt = prior.clarification or prior.summary
    transcript, voice_status = render_voice_strategy_controls(
        {"Hinglish / Indian English": "en-IN", "Hindi": "hi-IN",
         "English": "en-IN"}[voice_language],
        voice_prompt,
    )
    if transcript and transcript != st.session_state.get("last_strategy_transcript"):
        st.session_state.strategy_text = transcript
        st.session_state.last_strategy_transcript = transcript
        _reset_generated()
    if voice_status:
        st.caption(voice_status)

    text = st.text_area(
        "Plain English strategy", key="strategy_text", max_chars=500, height=90,
        placeholder="Try: BTC me EMA 20 50 ko cross kare to buy; RSI 30 ke neeche buy.",
    )
    if st.button("Generate strategy", key="builder_generate", type="primary",
                 width="stretch"):
        _interpret(selected_example or text)
        if voice_reply:
            st.rerun()


def _select_example(example: str) -> None:
    """Load an example and clear any stale generated strategy."""
    st.session_state.strategy_text = example
    st.session_state._selected_example = example
    _reset_generated()


def _reset_generated() -> None:
    st.session_state.generated_strategy = None
    st.session_state.strategy_interpretation = None
    st.session_state.strategy_error = None
    st.session_state.interpreted_strategy_text = None
    st.session_state.pop("strategy_rules_json", None)
    st.session_state.pop("generated_code", None)


def _interpret(text: str) -> None:
    """Interpret the description into a validated specification."""
    try:
        interpreted = interpret_strategy(text)
    except Exception:
        LOGGER.exception("Strategy interpretation failed")
        st.session_state.strategy_error = (
            "I couldn't finish interpreting that strategy. Try adding the entry "
            "trigger in everyday language."
        )
        return
    st.session_state.strategy_interpretation = interpreted
    st.session_state.interpreted_strategy_text = text
    st.session_state.generated_strategy = interpreted.specification
    st.session_state.strategy_error = None
    if interpreted.validation_message:
        st.session_state.strategy_error = interpreted.validation_message


def _review(symbol: str, timeframe: str, starting_capital: float) -> None:
    """Step two: confirm the interpretation, edit the rules, then run it."""
    strategy = st.session_state.get("generated_strategy")
    interpretation = st.session_state.get("strategy_interpretation")
    text = st.session_state.get("strategy_text", "")
    if (interpretation
            and st.session_state.get("interpreted_strategy_text") is not None
            and text.strip() != st.session_state.interpreted_strategy_text.strip()):
        # The description changed, so any generated strategy is now stale.
        strategy = interpretation = None
        st.session_state.strategy_error = None
        st.info("The description changed. Generate the strategy again to refresh "
                "and validate it before running.")
    if st.session_state.get("strategy_error") and not (
            interpretation and interpretation.validation_message):
        st.warning(st.session_state.strategy_error)
    if interpretation and interpretation.clarification:
        st.info(interpretation.clarification)
        if interpretation.suggestions:
            st.caption("Helpful confirmations: " + " · ".join(interpretation.suggestions))
        if "Use the complete existing ARJUNA strategy" in interpretation.suggestions:
            st.button("Open ARJUNA Strategy", key="open_ict_from_builder",
                      on_click=lambda: st.session_state.update(
                          {"strategy_choice": "ARJUNA Strategy"}))
        else:
            st.caption("Add your answer to the strategy description, then choose "
                       "Generate strategy.")
    if interpretation and interpretation.draft:
        _confirmation(interpretation)
    if strategy:
        _rules(symbol, timeframe, starting_capital)


def _confirmation(interpretation) -> None:
    """Show what was understood, plus the fixed safety limits."""
    candidate = interpretation.draft
    ui.html_block(ui.section_head("Strategy confirmation", "What the platform understood"))
    st.markdown(f"**Strategy understood:** {interpretation.summary}")
    if interpretation.validation_message:
        st.error("Safety validation: " + interpretation.validation_message)
    risk = candidate.get("risk_fraction", 0.01)
    rr = candidate.get("rr", 2.0)
    ui.html_block(ui.grid(4,
        ui.card(ui.stat("Risk", f"{float(risk) * 100:g}%")),
        ui.card(ui.stat("Target", f"{float(rr):g}R")),
        ui.card(ui.stat("Session", "24H")),
        ui.card(ui.stat("Mode", "Paper Trading")),
    ))
    st.caption("The fixed demo limit is 1% risk, minimum 1.5R, maximum 1x leverage. "
               "No live orders.")


def _rules(symbol: str, timeframe: str, starting_capital: float) -> None:
    """Step three: edit the validated rules, then backtest or paper-trade."""
    strategy = st.session_state["generated_strategy"]
    interpretation = st.session_state.get("strategy_interpretation")
    strategy_symbol = (interpretation.symbol if interpretation and interpretation.symbol
                       else symbol)
    strategy_timeframe = (interpretation.timeframe if interpretation
                          and interpretation.timeframe else timeframe)
    ui.html_block(ui.section_head("Validated strategy rules", "Review before running"))
    st.text_input("Strategy name", value=st.session_state.get("strategy_name",
                                                              "My custom strategy"),
                  key="strategy_name")
    encoded = json.dumps(strategy, indent=2)
    if st.session_state.get("strategy_rules_source") != encoded:
        st.session_state.strategy_rules_json = encoded
        st.session_state.strategy_rules_source = encoded
    edited = st.text_area("Review and edit the strategy rules (JSON)",
                          key="strategy_rules_json", height=190)
    edit_col, code_col, save_col = st.columns(3)
    apply_edits = edit_col.button("Validate", width="stretch")
    make_code = code_col.button("Generate code preview", width="stretch")
    save_version = save_col.button("Save strategy version", width="stretch")
    action_cols = st.columns(3)
    backtest = action_cols[0].button("Backtest", type="primary", width="stretch")
    paper_run = action_cols[1].button("Paper Trade", width="stretch")
    action_cols[2].button("Edit", width="stretch", on_click=_reset_generated)

    if apply_edits:
        _validate_edits(edited, interpretation, strategy_symbol, strategy_timeframe)
    if make_code:
        try:
            st.session_state.generated_code = generate_strategy_code(
                parse_strategy_json(edited))
        except StrategyError as exc:
            st.warning(str(exc))
    if save_version:
        _save_version(edited, strategy_symbol, strategy_timeframe)
    if st.session_state.get("generated_code"):
        st.code(st.session_state.generated_code, language="python")
    st.json(strategy, expanded=True)
    st.success("Strategy validated. Fixed demo risk limits apply to all runs.")

    if backtest or paper_run:
        _run(strategy, edited, strategy_symbol, strategy_timeframe,
             starting_capital, backtest, paper_run)


def _validate_edits(edited: str, interpretation, strategy_symbol: str,
                    strategy_timeframe: str) -> None:
    """Re-parse edited rules through the existing validator."""
    try:
        strategy = parse_strategy_json(edited)
    except StrategyError as exc:
        st.warning(str(exc))
        return
    st.session_state.generated_strategy = strategy
    st.session_state.strategy_rules_source = json.dumps(strategy, indent=2)
    if interpretation:
        interpretation.specification = strategy
        interpretation.draft = strategy
        interpretation.summary = summarize_strategy(strategy, strategy_symbol,
                                                    strategy_timeframe)
        interpretation.clarification = None
        interpretation.validation_message = None
    st.success("Edited rules validated. They are ready for backtest or paper trading.")


def _save_version(edited: str, strategy_symbol: str, strategy_timeframe: str) -> None:
    """Store a named version of the strategy in this browser session."""
    try:
        checked = parse_strategy_json(edited)
    except StrategyError as exc:
        st.warning(str(exc))
        return
    st.session_state.generated_strategy = checked
    st.session_state.strategy_rules_source = json.dumps(checked, indent=2)
    versions = st.session_state.setdefault("strategy_library", [])
    name = st.session_state.get("strategy_name", "Custom strategy")
    matching = [item for item in versions
                if item["name"] == name and item["market"] == strategy_symbol
                and item["timeframe"] == strategy_timeframe]
    version = len(matching) + 1
    versions.append({
        "name": name.strip() or "Custom strategy",
        "market": strategy_symbol, "timeframe": strategy_timeframe,
        "version": version,
        "saved_at": datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d %H:%M IST"),
        "spec": checked,
    })
    st.success(f"Saved {name} v{version} in this browser session.")


def _run(strategy: dict, edited: str, strategy_symbol: str, strategy_timeframe: str,
         starting_capital: float, backtest: bool, paper_run: bool) -> None:
    """Backtest and/or paper-trade the validated custom strategy."""
    from demo_safety import assert_demo_mode

    try:
        strategy = parse_strategy_json(edited)
        st.session_state.generated_strategy = strategy
        assert_demo_mode()
    except (StrategyError, SafetyError) as exc:
        st.warning(str(exc))
        return
    except Exception:
        LOGGER.exception("Custom strategy preparation failed")
        st.error("The strategy could not be prepared. Check the rules and try again.")
        return

    result = marketdata.load(strategy_symbol, strategy_timeframe,
                             marketdata.BACKTEST_CANDLES)
    if not result.ok:
        ui.html_block(ui.error_state("Market data unavailable", result.error))
        return
    st.caption(f"Data source: {result.source} · {len(result.frame)} finalized candles · UTC")
    marketdata.publish_chart_snapshot(result)
    data = result.frame

    if backtest:
        _backtest(data, strategy, float(starting_capital))
    if paper_run:
        _paper(data, strategy, strategy_symbol, strategy_timeframe,
               float(starting_capital), result.source)


def _backtest(data, strategy: dict, starting_capital: float) -> None:
    """Historical simulation of the custom strategy."""
    try:
        metrics, equity, trades = run_backtest(data, strategy,
                                               starting_capital=starting_capital)
    except (MarketDataError, ValueError) as exc:
        ui.html_block(ui.error_state("Backtest could not run", str(exc)))
        return
    ui.html_block(ui.section_head("Backtest performance", "Historical simulation"))
    cards = []
    for name in ("Total return %", "Total P&L", "Maximum drawdown %",
                 "Win rate %", "Number of trades"):
        value = metrics[name]
        if name == "Total P&L":
            body = f'<span class="{ui.tone_class(value)}">{ui.money(value)}</span>'
        elif name.endswith("%"):
            body = f'<span class="{ui.tone_class(value)}">{ui.percent(value)}</span>'
        else:
            body = ui.esc(str(value))
        cards.append(ui.card(ui.stat(name, body)))
    ui.html_block(ui.grid(3, *cards))
    st.caption(f"Starting ${metrics['Starting capital']:,.2f} · ending "
               f"${metrics['Ending capital']:,.2f} · wins {metrics['Wins']} · losses "
               f"{metrics['Losses']} · profit factor {metrics['Profit factor']:.2f}")
    if equity is not None and not equity.empty:
        ui.html_block(ui.card(ui.area_chart(
            [(str(index)[:16], float(value))
             for index, value in equity["equity"].items()], tone="accent"),
            variant="flat"))
    if trades is not None and not trades.empty:
        st.dataframe(trades, width="stretch", hide_index=True)
    st.caption("Signals use finalized candles; fills occur at the next candle open "
               "with 0.01% slippage and 0.04% fees. If the stop and target fall "
               "within one candle, the stop is assumed first.")


def _paper(data, strategy: dict, strategy_symbol: str, strategy_timeframe: str,
           starting_capital: float, source: str) -> None:
    """Advance the custom strategy's paper account by one candle."""
    account = state.get_paper_account("custom.dsl", starting_capital,
                                      strategy_symbol, strategy_timeframe)
    context = {
        "strategy_id": "custom.dsl", "strategy_version": "1.0.0",
        "market": strategy_symbol, "timeframe": strategy_timeframe,
        "data_source": source,
        "entry_rules": json.dumps(strategy["entry"], sort_keys=True),
        "market_context": f"close={float(data.close.iloc[-1]):.8g}",
    }
    try:
        message = advance_paper_account(data, strategy, account, journal_context=context)
    except (MarketDataError, ValueError) as exc:
        ui.html_block(ui.error_state("Paper trading blocked", str(exc)))
        return
    ui.html_block(ui.section_head("PAPER TRADING", "Simulation only"))
    st.info(message)
    render_account_state(account, "AI Strategy")