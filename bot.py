import time
from datetime import datetime, timezone

import ccxt

from config import (
    SYMBOL,
    HTF_TIMEFRAME,
    LTF_TIMEFRAME,
    HTF_LIMIT,
    LTF_LIMIT,
    MIN_RR,
    DEFAULT_RR,
    PAPER_TRADING,
    MAX_TRADES_PER_DAY,
    MAX_DAILY_LOSS_R,
    NEWS_FILTER_ENABLED,
    NEWS_BLACKOUT,
    SIGNAL_COOLDOWN_MINUTES,
)

# ============================================================
# STRATEGIES
# ============================================================

from strategies.trend import trend_signal
from strategies.momentum import momentum_signal
from strategies.volatility import volatility_signal
from strategies.breakout import breakout_signal
from strategies.vwap import vwap_signal
from strategies.volume import volume_signal
from strategies.price_action import price_action_signal
from strategies.mean_reversion import mean_reversion_signal
from strategies.ict import ict_signal
from strategies.wyckoff import wyckoff_signal

# New quantitative strategies
from strategies.kama import kama_signal
from strategies.donchian import donchian_signal
from strategies.divergence import divergence_signal
from strategies.cambridge_hook import cambridge_hook_signal

# Crypto-specific strategy
from strategies.crypto import (
    get_crypto_market_data,
    calculate_oi_change,
    calculate_price_change,
    crypto_signal,
)

# ============================================================
# ENGINE
# ============================================================

from engine.regime import detect_regime
from engine.scorer import score_strategies

from engine.risk import (
    calculate_atr,
    calculate_atr_levels,
    calculate_position_size,
    calculate_risk_amount,
    calculate_notional,
    validate_trade,
)

from engine.paper import PaperTrader
from demo_safety import DEMO_MODE, LIVE_ORDERS_ENABLED, assert_demo_mode
from demo_data import validate_ohlcv

MULTI_STRATEGY_FUNCTIONS = (
    ("TREND", "trend_signal"),
    ("MOMENTUM", "momentum_signal"),
    ("VOLATILITY", "volatility_signal"),
    ("BREAKOUT", "breakout_signal"),
    ("VWAP", "vwap_signal"),
    ("VOLUME", "volume_signal"),
    ("PRICE_ACTION", "price_action_signal"),
    ("MEAN_REVERSION", "mean_reversion_signal"),
    ("ICT", "ict_signal"),
    ("WYCKOFF", "wyckoff_signal"),
    ("KAMA", "kama_signal"),
    ("DONCHIAN", "donchian_signal"),
    ("DIVERGENCE", "divergence_signal"),
    ("CAMBRIDGE_HOOK", "cambridge_hook_signal"),
)


def multi_strategy_names():
    """Return the engine's current strategy keys, including its crypto signal."""
    return tuple(name for name, _ in MULTI_STRATEGY_FUNCTIONS) + ("CRYPTO",)


# ============================================================
# BOT CONFIG
# ============================================================

ACCOUNT_BALANCE = 10000.0

# 1% account risk per trade
RISK_PERCENT = 1.0

# Spot mode = effectively 1x
MAX_LEVERAGE = 1.0

# ATR risk management
ATR_PERIOD = 14
ATR_SL_MULTIPLIER = 1.5

# Scan interval
POLL_SECONDS = 60

# Estimated Binance trading costs
FEE_RATE = 0.0004
SLIPPAGE = 0.0001


# ============================================================
# EXCHANGE
# ============================================================

exchange = ccxt.binance({
    "enableRateLimit": True,
    "options": {
        "defaultType": "spot",
    },
})


# ============================================================
# PAPER TRADER
# ============================================================

paper_trader = PaperTrader(
    starting_balance=ACCOUNT_BALANCE,
    fee_rate=FEE_RATE,
    slippage=SLIPPAGE,
)


# ============================================================
# BOT STATE
# ============================================================

last_trade_time = None

previous_oi = None
previous_price = None


# ============================================================
# SAFE OHLCV FETCH
# ============================================================

def fetch_ohlcv_safe(symbol, timeframe, limit):
    """
    Fetch OHLCV data safely.

    The newest exchange candle can still be forming.
    Therefore the newest candle is removed.

    All strategy calculations operate on finalized candles.
    """

    for attempt in range(3):

        try:

            candles = exchange.fetch_ohlcv(
                symbol,
                timeframe=timeframe,
                limit=limit,
            )

            if candles and len(candles) > 2:

                # Remove currently forming candle.
                finalized = candles[:-1]

                # Reject malformed/stale-shaped data before any strategy sees it.
                validate_ohlcv(finalized, minimum=35)

                return finalized

        except Exception as e:

            print(
                f"Data fetch error "
                f"({timeframe}) "
                f"attempt {attempt + 1}/3: {e}"
            )

            if attempt < 2:
                time.sleep(3)

    return None


# ============================================================
# COOLDOWN
# ============================================================

def cooldown_remaining():

    if last_trade_time is None:
        return 0.0

    elapsed_minutes = (
        datetime.now(timezone.utc) - last_trade_time
    ).total_seconds() / 60.0

    remaining = (
        SIGNAL_COOLDOWN_MINUTES
        - elapsed_minutes
    )

    return max(0.0, remaining)


# ============================================================
# TRADE RESULT DISPLAY
# ============================================================

def print_trade_result(result):

    if not result:
        return

    print("\n========== TRADE CLOSED ==========")

    reason = result.get(
        "reason",
        "",
    )

    if reason == "TP":
        print("Result       : WIN")

    elif reason == "SL":
        print("Result       : LOSS")

    else:
        print("Result       : CLOSED")

    print(
        f"Reason       : {reason}"
    )

    print(
        f"Gross P&L    : "
        f"${result.get('gross_pnl', 0):.2f}"
    )

    print(
        f"Fees         : "
        f"${result.get('fees', 0):.2f}"
    )

    print(
        f"Net P&L      : "
        f"${result.get('net_pnl', 0):.2f}"
    )

    print(
        f"Gross R      : "
        f"{result.get('gross_r', 0):.3f}"
    )

    print(
        f"Net R        : "
        f"{result.get('net_r', 0):.3f}"
    )

    print(
        f"Balance      : "
        f"${paper_trader.balance:.2f}"
    )

    print("==================================")


# ============================================================
# OPEN POSITION MONITOR
# ============================================================

def monitor_position(candle):

    if paper_trader.position is None:
        return

    position = paper_trader.position

    side = position["side"]
    stop = position["stop"]
    target = position["target"]

    high = float(candle[2])
    low = float(candle[3])

    # ========================================================
    # LONG POSITION
    # ========================================================

    if side == "LONG":

        # If SL and TP are both touched in the same
        # candle, assume SL happened first.
        if low <= stop:

            result = paper_trader.close_position(
                exit_price=stop,
                reason="SL",
            )

            print_trade_result(result)

            if result:
                paper_trader.print_statistics()

            return

        if high >= target:

            result = paper_trader.close_position(
                exit_price=target,
                reason="TP",
            )

            print_trade_result(result)

            if result:
                paper_trader.print_statistics()

            return

    # ========================================================
    # SHORT POSITION
    # ========================================================

    elif side == "SHORT":

        # SL priority if both levels are touched.
        if high >= stop:

            result = paper_trader.close_position(
                exit_price=stop,
                reason="SL",
            )

            print_trade_result(result)

            if result:
                paper_trader.print_statistics()

            return

        if low <= target:

            result = paper_trader.close_position(
                exit_price=target,
                reason="TP",
            )

            print_trade_result(result)

            if result:
                paper_trader.print_statistics()

            return


# ============================================================
# CRYPTO MARKET SIGNAL
# ============================================================

def get_crypto_signal(current_price):

    global previous_oi
    global previous_price

    try:

        data = get_crypto_market_data()

    except Exception as e:

        return {
            "side": "NEUTRAL",
            "score": 0,
            "reason": f"Crypto data error: {e}",
            "funding_rate": None,
            "open_interest": None,
            "oi_change": None,
            "price_change": None,
        }

    if not data.get("available", False):

        return {
            "side": "NEUTRAL",
            "score": 0,
            "reason": "Funding/OI data unavailable",
            "funding_rate": None,
            "open_interest": None,
            "oi_change": None,
            "price_change": None,
        }

    current_funding = data.get(
        "funding_rate"
    )

    current_oi = data.get(
        "open_interest"
    )

    oi_change = calculate_oi_change(
        current_oi,
        previous_oi,
    )

    price_change = calculate_price_change(
        previous_price,
        current_price,
    )

    signal = crypto_signal(
        funding_rate=current_funding,
        open_interest_change=oi_change,
        price_change=price_change,
    )

    if not isinstance(signal, dict):

        signal = {
            "side": "NEUTRAL",
            "score": 0,
            "reason": "Invalid crypto strategy response",
        }

    signal["funding_rate"] = current_funding
    signal["open_interest"] = current_oi
    signal["oi_change"] = oi_change
    signal["price_change"] = price_change

    previous_oi = current_oi
    previous_price = current_price

    return signal


# ============================================================
# SAFE STRATEGY CALL
# ============================================================

def safe_strategy_call(
    strategy_name,
    strategy_function,
    candles,
):
    """
    Prevent one broken strategy from stopping
    the complete multi-strategy engine.
    """

    try:

        result = strategy_function(candles)

        if not isinstance(result, dict):

            return {
                "side": "NEUTRAL",
                "score": 0,
                "reason": (
                    f"{strategy_name}: "
                    "invalid strategy response"
                ),
            }

        # Make sure required fields exist.
        result.setdefault(
            "side",
            "NEUTRAL",
        )

        result.setdefault(
            "score",
            0,
        )

        result.setdefault(
            "reason",
            "",
        )

        return result

    except Exception as e:

        print(
            f"{strategy_name} ERROR: {e}"
        )

        return {
            "side": "NEUTRAL",
            "score": 0,
            "reason": (
                f"{strategy_name} error: {e}"
            ),
        }


def analyze_multi_strategy_candles(candles, strategy_selection=None, historical=False):
    """Run the existing regime, strategy, and aggregation pipeline on finalized OHLCV.

    Historical callers can suppress the live funding/open-interest request. The
    CRYPTO slot remains present but neutral unless a historical series is added.
    """
    if candles is None or len(candles) < 2:
        raise ValueError("At least two finalized candles are required for multi-strategy analysis.")

    price = float(candles[-1][4])
    regime_data = detect_regime(candles)
    regime = regime_data.get("regime", "UNKNOWN")
    signals = {
        name: safe_strategy_call(name, globals()[function_name], candles)
        for name, function_name in MULTI_STRATEGY_FUNCTIONS
    }
    if historical:
        signals["CRYPTO"] = {
            "side": "NEUTRAL", "score": 0,
            "reason": "Historical funding/open-interest data is unavailable.",
        }
    else:
        signals["CRYPTO"] = get_crypto_signal(price)

    scoring_signals = signals
    if strategy_selection is not None:
        selected = set(strategy_selection)
        unknown = selected - set(signals)
        if unknown:
            raise ValueError(f"Unknown strategy selection: {', '.join(sorted(unknown))}")
        scoring_signals = {name: value for name, value in signals.items() if name in selected}

    try:
        final_signal = score_strategies(scoring_signals, regime)
    except TypeError:
        final_signal = score_strategies(strategy_signals=scoring_signals, regime=regime)
    if not isinstance(final_signal, dict):
        raise ValueError("The multi-strategy scorer returned an invalid result.")

    side = final_signal.get("side", "NEUTRAL")
    active = final_signal.get("active_long" if side == "LONG" else "active_short", [])
    display_active = ["ARJUNA" if name == "ICT" else name for name in active]
    reason = final_signal.get("reason") or (
        f"Weighted {side.lower()} consensus from {', '.join(display_active)}."
        if side in {"LONG", "SHORT"} and active
        else (
            "No directional agreement among active strategies. "
            f"Long: {', '.join('ARJUNA' if name == 'ICT' else name for name in final_signal.get('active_long', [])) or 'none'}; "
            f"short: {', '.join('ARJUNA' if name == 'ICT' else name for name in final_signal.get('active_short', [])) or 'none'}."
        )
    )
    final_signal = {**final_signal, "reason": reason}
    return {
        "price": price,
        "regime": regime_data,
        "signals": signals,
        "scored_strategies": list(scoring_signals),
        "final_signal": final_signal,
    }


def build_multi_strategy_trade_levels(candles, side):
    """Use the bot's existing ATR, 2R default, and 1.5R minimum risk rules."""
    if side not in {"LONG", "SHORT"}:
        raise ValueError("No valid direction")
    if candles is None or len(candles) < ATR_PERIOD + 1:
        raise ValueError("ATR unavailable")
    price = float(candles[-1][4])
    atr = calculate_atr(candles, ATR_PERIOD)
    if atr is None or float(atr) <= 0:
        raise ValueError("ATR unavailable")
    atr = float(atr)
    levels = calculate_atr_levels(
        entry=price, atr=atr, side=side,
        sl_multiplier=ATR_SL_MULTIPLIER, rr=DEFAULT_RR,
    )
    if levels is None:
        raise ValueError("Could not calculate ATR levels")
    valid, rr, reason = validate_trade(
        entry=levels["entry"], stop=levels["stop"], target=levels["target"],
        side=side, min_rr=MIN_RR,
    )
    if not valid:
        raise ValueError(reason)
    return {**levels, "atr": atr, "rr": rr}


# ============================================================
# MAIN ANALYSIS
# ============================================================

def run_analysis():

    global last_trade_time

    if not DEMO_MODE or LIVE_ORDERS_ENABLED:
        print("TRADE BLOCKED: demo safety lock is not satisfied")
        return

    print("\n" + "=" * 70)

    print(
        "BTC/USDT MULTI-STRATEGY "
        "PAPER TRADING BOT"
    )

    print("=" * 70)

    print(
        f"Minimum R:R : {MIN_RR}"
    )

    print(
        f"Paper Mode  : {PAPER_TRADING}"
    )

    print(
        f"Risk/Trade  : {RISK_PERCENT}%"
    )

    print(
        f"Max Trades  : {MAX_TRADES_PER_DAY}"
    )

    print(
        f"Max Daily R : -{MAX_DAILY_LOSS_R}R"
    )

    # ========================================================
    # FETCH LTF
    # ========================================================

    ltf = fetch_ohlcv_safe(
        SYMBOL,
        LTF_TIMEFRAME,
        LTF_LIMIT,
    )

    if ltf is None:

        print(
            "Could not fetch LTF candles."
        )

        return

    # ========================================================
    # FETCH HTF
    # ========================================================

    htf = fetch_ohlcv_safe(
        SYMBOL,
        HTF_TIMEFRAME,
        HTF_LIMIT,
    )

    if htf is None:

        print(
            "Could not fetch HTF candles."
        )

        return

    # Keep HTF available for future
    # multi-timeframe strategy upgrades.
    _ = htf

    # ========================================================
    # PRICE
    # ========================================================

    if len(ltf) < 2:

        print(
            "TRADE BLOCKED: insufficient LTF data"
        )

        return

    latest_candle = ltf[-1]

    price = float(
        latest_candle[4]
    )

    candle_time = datetime.fromtimestamp(
        latest_candle[0] / 1000,
        tz=timezone.utc,
    )

    print(
        f"\n"
        f"{candle_time.strftime('%Y-%m-%d %H:%M:%S')}"
        f" UTC"
    )

    print(
        f"PRICE {price:.2f}"
    )

    # ========================================================
    # EXISTING POSITION
    # ========================================================

    if paper_trader.position is not None:

        position = paper_trader.position

        print("\nOPEN POSITION")

        print(
            f"Side       : "
            f"{position['side']}"
        )

        print(
            f"Entry      : "
            f"{position['entry']:.2f}"
        )

        print(
            f"SL         : "
            f"{position['stop']:.2f}"
        )

        print(
            f"Target     : "
            f"{position['target']:.2f}"
        )

        monitor_position(
            latest_candle
        )

        # Never open a second position.
        if paper_trader.position is not None:

            return

    try:
        analysis = analyze_multi_strategy_candles(ltf)
    except Exception as e:
        print(f"MULTI-STRATEGY ANALYSIS ERROR: {e}")
        return

    regime_data = analysis["regime"]
    regime = regime_data.get("regime", "UNKNOWN")
    signals = analysis["signals"]
    final_signal = analysis["final_signal"]

    print(f"\nREGIME {regime} ({regime_data.get('score', 0)})")
    print(f"Regime Reason: {regime_data.get('reason', '')}")
    print("\nSTRATEGY SIGNALS")
    print("-" * 70)
    for name, signal in signals.items():
        try:
            score_display = f"{float(signal.get('score', 0)):.0f}"
        except (TypeError, ValueError):
            score_display = "0"
        display_name = "ARJUNA" if name == "ICT" else name
        display_reason = str(signal.get("reason", "")).replace("ICT", "ARJUNA")
        print(f"{display_name:<18} {signal.get('side', 'NEUTRAL'):<8} {score_display:<5} {display_reason}")

    crypto = signals["CRYPTO"]
    if crypto.get("funding_rate") is not None:
        print("\nCRYPTO MARKET DATA")
        print("-" * 70)
        print(f"Funding Rate : {crypto['funding_rate']:.6f}")
        print(f"OI Change    : {crypto['oi_change']:.4f}%" if crypto.get("oi_change") is not None else "OI Change    : N/A (waiting for next sample)")
        print(f"Price Change : {crypto['price_change']:.4f}%" if crypto.get("price_change") is not None else "Price Change : N/A (waiting for next sample)")

    # ========================================================
    # FINAL SIGNAL DATA
    # ========================================================

    final_side = final_signal.get(
        "side",
        "NEUTRAL",
    )

    final_score = float(
        final_signal.get(
            "score",
            0,
        )
    )

    final_reason = final_signal.get(
        "reason",
        "",
    )

    confirmation_points = final_signal.get("confirmation_points")
    if confirmation_points is None:
        confirmation_points = (
            f"Long {final_signal.get('long_confirmation_points', 0)} / "
            f"Short {final_signal.get('short_confirmation_points', 0)}"
        )

    heavy_conditions = final_signal.get(
        "heavy_conditions",
        [],
    )
    if not heavy_conditions:
        heavy_conditions = sorted(set(
            final_signal.get("long_heavy_conditions_list", [])
            + final_signal.get("short_heavy_conditions_list", [])
        ))

    confirmation_passed = bool(
        final_signal.get(
            "confirmation_passed",
            False,
        )
    )

    normal_confirmation = bool(
        final_signal.get(
            "normal_confirmation",
            False,
        )
    )

    heavy_confirmation = bool(
        final_signal.get(
            "heavy_confirmation",
            False,
        )
    )

    # ========================================================
    # FINAL SIGNAL DISPLAY
    # ========================================================

    print("\nFINAL SIGNAL")

    print("-" * 70)

    print(
        f"FINAL SIGNAL "
        f"{final_side} "
        f"{final_score:.2f}"
    )

    if final_reason:

        print(
            f"Reason: {final_reason}"
        )

    print(
        f"Confirmation Points : "
        f"{confirmation_points}"
    )

    print(
        f"Heavy Conditions    : "
        f"{len(heavy_conditions)}"
    )

    if heavy_conditions:

        display_heavy_conditions = ["ARJUNA" if name == "ICT" else name for name in heavy_conditions]
        print(
            f"Heavy List          : "
            f"{', '.join(display_heavy_conditions)}"
        )

    print(
        f"Normal Confirmation : "
        f"{normal_confirmation}"
    )

    print(
        f"Heavy Confirmation  : "
        f"{heavy_confirmation}"
    )

    # ========================================================
    # DIRECTION CHECK
    # ========================================================

    if final_side not in (
        "LONG",
        "SHORT",
    ):

        print(
            "\nTRADE BLOCKED: "
            "NO VALID DIRECTION"
        )

        return

    # ========================================================
    # HARD CONFIRMATION GATE
    # ========================================================
    #
    # Trade allowed when:
    #
    # 1. 4 or more confirmation points
    #
    # OR
    #
    # 2. 2 independent heavy strategies
    #
    # Heavy strategies:
    #
    # ICT
    # Cambridge Hook
    # Donchian
    # Divergence
    #
    # A strategy's internal conditions do NOT
    # count separately.
    # ========================================================

    if not confirmation_passed:

        print(
            "\nTRADE BLOCKED: "
            "INSUFFICIENT CONFIRMATION"
        )

        print(
            f"Confirmation Points : "
            f"{confirmation_points}"
        )

        print(
            f"Heavy Conditions    : "
            f"{len(heavy_conditions)}"
        )

        print(
            "Required            : "
            "4+ points OR 2 heavy conditions"
        )

        return

    print(
        "\nCONFIRMATION PASSED"
    )

    print(
        f"Points              : "
        f"{confirmation_points}"
    )

    print(
        f"Heavy Conditions    : "
        f"{len(heavy_conditions)}"
    )

    # ========================================================
    # NEWS FILTER
    # ========================================================

    if (
        NEWS_FILTER_ENABLED
        and NEWS_BLACKOUT
    ):

        print(
            "\nTRADE BLOCKED: "
            "NEWS BLACKOUT ACTIVE"
        )

        return

    # ========================================================
    # COOLDOWN
    # ========================================================

    remaining = cooldown_remaining()

    if remaining > 0:

        print(
            f"\nTRADE BLOCKED: "
            f"cooldown "
            f"{remaining:.1f} min remaining"
        )

        return

    # ========================================================
    # DAILY TRADE LIMIT
    # ========================================================

    if (
        paper_trader.daily_trade_count
        >= MAX_TRADES_PER_DAY
    ):

        print(
            "\nTRADE BLOCKED: "
            "MAX DAILY TRADES REACHED"
        )

        return

    # ========================================================
    # DAILY LOSS CIRCUIT BREAKER
    # ========================================================

    if (
        paper_trader.daily_r
        <= -abs(MAX_DAILY_LOSS_R)
    ):

        print(
            "\nTRADE BLOCKED: "
            "MAX DAILY LOSS REACHED"
        )

        return

    try:
        levels = build_multi_strategy_trade_levels(ltf, final_side)
    except (TypeError, ValueError, KeyError) as e:
        print(f"\nTRADE BLOCKED: {e}")
        return

    atr = levels["atr"]
    entry = float(levels["entry"])
    stop = float(levels["stop"])
    target = float(levels["target"])
    rr = float(levels["rr"])
    print(f"\nATR {atr:.4f}")
    print(f"R:R {rr:.2f} VALID")

    # ========================================================
    # RISK AMOUNT
    # ========================================================

    risk_amount = calculate_risk_amount(
        paper_trader.balance,
        RISK_PERCENT,
    )

    if risk_amount <= 0:

        print(
            "\nTRADE BLOCKED: "
            "Invalid risk amount"
        )

        return

    # ========================================================
    # POSITION SIZE
    # ========================================================

    quantity = calculate_position_size(
        account_balance=paper_trader.balance,
        risk_percent=RISK_PERCENT,
        entry=entry,
        stop=stop,
        max_leverage=MAX_LEVERAGE,
    )

    if quantity <= 0:

        print(
            "\nTRADE BLOCKED: "
            "Invalid position size"
        )

        return

    # ========================================================
    # NOTIONAL
    # ========================================================

    notional = calculate_notional(
        entry,
        quantity,
    )

    if notional <= 0:

        print(
            "\nTRADE BLOCKED: "
            "Invalid notional"
        )

        return

    print(
        f"Risk       ${risk_amount:.2f}"
    )

    print(
        f"Quantity   {quantity:.8f}"
    )

    print(
        f"Notional   ${notional:.2f}"
    )

    # ========================================================
    # LEVERAGE / NOTIONAL SAFETY
    # ========================================================

    max_notional = (
        paper_trader.balance
        * MAX_LEVERAGE
    )

    if notional > max_notional:

        print(
            "\nTRADE BLOCKED: "
            "Notional exceeds leverage limit"
        )

        print(
            f"Maximum Notional : "
            f"${max_notional:.2f}"
        )

        print(
            f"Requested        : "
            f"${notional:.2f}"
        )

        return

    # ========================================================
    # PAPER TRADING ONLY
    # ========================================================

    if PAPER_TRADING:

        assert_demo_mode()

        success = paper_trader.open_position(
            side=final_side,
            entry=entry,
            stop=stop,
            target=target,
            score=final_score,
            risk_amount=risk_amount,
            quantity=quantity,
        )

        if not success:

            print(
                "\nTRADE BLOCKED: "
                "Paper trader rejected position"
            )

            return

        last_trade_time = datetime.now(
            timezone.utc
        )

        print(
            "\n========== POSITION OPENED =========="
        )

        print(
            f"Side       : "
            f"{final_side}"
        )

        print(
            f"Signal     : "
            f"{final_score:.2f}"
        )

        print(
            f"Confirm    : "
            f"{confirmation_points} points"
        )

        print(
            f"Entry      : "
            f"{entry:.2f}"
        )

        print(
            f"SL         : "
            f"{stop:.2f}"
        )

        print(
            f"Target     : "
            f"{target:.2f}"
        )

        print(
            f"R:R        : "
            f"{rr:.2f}"
        )

        print(
            f"Risk       : "
            f"${risk_amount:.2f}"
        )

        print(
            f"Quantity   : "
            f"{quantity:.8f}"
        )

        print(
            f"Notional   : "
            f"${notional:.2f}"
        )

        print(
            f"Balance    : "
            f"${paper_trader.balance:.2f}"
        )

        print(
            "======================================"
        )

    else:

        # Intentionally no live execution.
        print(
            "\nREAL TRADING DISABLED."
        )


# ============================================================
# MAIN LOOP
# ============================================================

def main():

    print("\n")

    print("=" * 70)

    print(
        "STARTING BOT"
    )

    print("=" * 70)

    print(
        f"Symbol       : "
        f"{SYMBOL}"
    )

    print(
        f"HTF          : "
        f"{HTF_TIMEFRAME}"
    )

    print(
        f"LTF          : "
        f"{LTF_TIMEFRAME}"
    )

    print(
        f"Paper        : "
        f"{PAPER_TRADING}"
    )

    print(
        f"Poll         : "
        f"{POLL_SECONDS} seconds"
    )

    print(
        "Real trading : DISABLED"
    )

    print("=" * 70)

    while True:

        try:

            run_analysis()

        except KeyboardInterrupt:

            print(
                "\nBot stopped by user."
            )

            break

        except Exception as e:

            print(
                f"\nMAIN LOOP ERROR: {e}"
            )

        print(
            f"\nNext scan in "
            f"{POLL_SECONDS} seconds..."
        )

        time.sleep(
            POLL_SECONDS
        )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    main()


