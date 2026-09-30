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

    # ========================================================
    # MARKET REGIME
    # ========================================================

    try:

        regime_data = detect_regime(
            ltf
        )

    except Exception as e:

        print(
            f"REGIME ERROR: {e}"
        )

        return

    regime = regime_data.get(
        "regime",
        "UNKNOWN",
    )

    print(
        f"\nREGIME {regime} "
        f"({regime_data.get('score', 0)})"
    )

    print(
        f"Regime Reason: "
        f"{regime_data.get('reason', '')}"
    )

    # ========================================================
    # STRATEGY SIGNALS
    # ========================================================

    signals = {}

    # ========================================================
    # NORMAL STRATEGIES
    # ========================================================

    signals["TREND"] = safe_strategy_call(
        "TREND",
        trend_signal,
        ltf,
    )

    signals["MOMENTUM"] = safe_strategy_call(
        "MOMENTUM",
        momentum_signal,
        ltf,
    )

    signals["VOLATILITY"] = safe_strategy_call(
        "VOLATILITY",
        volatility_signal,
        ltf,
    )

    signals["BREAKOUT"] = safe_strategy_call(
        "BREAKOUT",
        breakout_signal,
        ltf,
    )

    signals["VWAP"] = safe_strategy_call(
        "VWAP",
        vwap_signal,
        ltf,
    )

    signals["VOLUME"] = safe_strategy_call(
        "VOLUME",
        volume_signal,
        ltf,
    )

    signals["PRICE_ACTION"] = safe_strategy_call(
        "PRICE_ACTION",
        price_action_signal,
        ltf,
    )

    signals["MEAN_REVERSION"] = safe_strategy_call(
        "MEAN_REVERSION",
        mean_reversion_signal,
        ltf,
    )

    signals["ICT"] = safe_strategy_call(
        "ICT",
        ict_signal,
        ltf,
    )

    signals["WYCKOFF"] = safe_strategy_call(
        "WYCKOFF",
        wyckoff_signal,
        ltf,
    )

    # ========================================================
    # NEW QUANT STRATEGIES
    # ========================================================

    # KAMA
    # Normal condition = 1 point

    signals["KAMA"] = safe_strategy_call(
        "KAMA",
        kama_signal,
        ltf,
    )

    # Donchian
    # Heavy condition = 2 points

    signals["DONCHIAN"] = safe_strategy_call(
        "DONCHIAN",
        donchian_signal,
        ltf,
    )

    # Momentum Divergence
    # Heavy condition = 2 points

    signals["DIVERGENCE"] = safe_strategy_call(
        "DIVERGENCE",
        divergence_signal,
        ltf,
    )

    # Cambridge Hook
    # Heavy condition = 2 points

    signals["CAMBRIDGE_HOOK"] = safe_strategy_call(
        "CAMBRIDGE_HOOK",
        cambridge_hook_signal,
        ltf,
    )

    # ========================================================
    # CRYPTO
    # ========================================================

    signals["CRYPTO"] = get_crypto_signal(
        price
    )

    # ========================================================
    # PRINT ALL STRATEGIES
    # ========================================================

    print("\nSTRATEGY SIGNALS")

    print("-" * 70)

    for name, signal in signals.items():

        side = signal.get(
            "side",
            "NEUTRAL",
        )

        score = signal.get(
            "score",
            0,
        )

        reason = signal.get(
            "reason",
            "",
        )

        try:
            score_display = f"{float(score):.0f}"

        except (TypeError, ValueError):
            score_display = "0"

        print(
            f"{name:<18} "
            f"{side:<8} "
            f"{score_display:<5} "
            f"{reason}"
        )

    # ========================================================
    # CRYPTO DETAILS
    # ========================================================

    crypto = signals["CRYPTO"]

    if crypto.get("funding_rate") is not None:

        print("\nCRYPTO MARKET DATA")

        print("-" * 70)

        print(
            f"Funding Rate : "
            f"{crypto['funding_rate']:.6f}"
        )

        if crypto.get("oi_change") is not None:

            print(
                f"OI Change    : "
                f"{crypto['oi_change']:.4f}%"
            )

        else:

            print(
                "OI Change    : N/A "
                "(waiting for next sample)"
            )

        if crypto.get("price_change") is not None:

            print(
                f"Price Change : "
                f"{crypto['price_change']:.4f}%"
            )

        else:

            print(
                "Price Change : N/A "
                "(waiting for next sample)"
            )

    # ========================================================
    # MULTI-STRATEGY SCORER
    # ========================================================

    try:

        final_signal = score_strategies(
            signals,
            regime,
        )

    except TypeError:

        final_signal = score_strategies(
            strategy_signals=signals,
            regime=regime,
        )

    except Exception as e:

        print(
            f"\nSCORER ERROR: {e}"
        )

        return

    if not isinstance(final_signal, dict):

        print(
            "\nTRADE BLOCKED: invalid scorer response"
        )

        return

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

    confirmation_points = int(
        final_signal.get(
            "confirmation_points",
            0,
        )
    )

    heavy_conditions = final_signal.get(
        "heavy_conditions",
        [],
    )

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

        print(
            f"Heavy List          : "
            f"{', '.join(heavy_conditions)}"
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

    # Outside Kill Zones, retain the existing gate for non-ICT signals. A valid
    # ICT signal aligned with the final direction may enter when the filter is off.
    utc_hour = datetime.now(timezone.utc).hour
    in_kill_zone = 7 <= utc_hour < 10 or 12 <= utc_hour < 15
    ict_side = signals.get("ICT", {}).get("side")
    if not in_kill_zone and ict_side != final_side:
        print("\nTRADE BLOCKED: outside London/New York kill zones (UTC); no aligned ICT setup")
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

    # ========================================================
    # ATR
    # ========================================================

    atr = calculate_atr(
        ltf,
        ATR_PERIOD,
    )

    if atr is None:

        print(
            "\nTRADE BLOCKED: "
            "ATR unavailable"
        )

        return

    try:
        atr = float(atr)
    except (TypeError, ValueError):

        print(
            "\nTRADE BLOCKED: "
            "Invalid ATR"
        )

        return

    if atr <= 0:

        print(
            "\nTRADE BLOCKED: "
            "ATR <= 0"
        )

        return

    print(
        f"\nATR {atr:.4f}"
    )

    # ========================================================
    # ATR SL / TP
    # ========================================================

    levels = calculate_atr_levels(
        entry=price,
        atr=atr,
        side=final_side,
        sl_multiplier=ATR_SL_MULTIPLIER,
        rr=MIN_RR,
    )

    if levels is None:

        print(
            "\nTRADE BLOCKED: "
            "Could not calculate ATR levels"
        )

        return

    try:

        entry = float(
            levels["entry"]
        )

        stop = float(
            levels["stop"]
        )

        target = float(
            levels["target"]
        )

    except (KeyError, TypeError, ValueError):

        print(
            "\nTRADE BLOCKED: "
            "Invalid ATR levels"
        )

        return

    # ========================================================
    # R:R VALIDATION
    # ========================================================

    valid, rr, validation_reason = validate_trade(
        entry=entry,
        stop=stop,
        target=target,
        side=final_side,
        min_rr=MIN_RR,
    )

    print(
        f"R:R {rr:.2f} "
        f"{'VALID' if valid else 'INVALID'}"
    )

    if not valid:

        print(
            f"TRADE BLOCKED: "
            f"{validation_reason}"
        )

        return

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


