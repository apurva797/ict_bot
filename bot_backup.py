import time
from datetime import datetime, timezone

import ccxt

import config

from strategy import (
    previous_day_high_low,
    asian_range,
    detect_liquidity_sweep,
    detect_displacement,
    detect_bullish_mss,
    detect_bearish_mss,
    find_bullish_fvgs,
    find_bearish_fvgs,
    find_recent_swing_high,
    find_recent_swing_low,
    find_bullish_order_block,
    find_bearish_order_block,
    in_discount,
    in_premium,
    calculate_ict_score,
)


# =========================================================
# EXCHANGE
# =========================================================

exchange = ccxt.binance({
    "enableRateLimit": True,
})


# =========================================================
# STATE
# =========================================================

last_signal_key = None
trades_today = 0
daily_r = 0.0
current_day = None


# =========================================================
# TIME
# =========================================================

def utc_now():

    return datetime.now(timezone.utc)


def reset_daily_limits():

    global trades_today
    global daily_r
    global current_day

    today = utc_now().date()

    if current_day != today:

        current_day = today
        trades_today = 0
        daily_r = 0.0

        print()
        print("New trading day.")
        print("Daily limits reset.")


# =========================================================
# KILL ZONE
# =========================================================

def in_kill_zone():

    hour = utc_now().hour

    london = (
        config.LONDON_START
        <= hour
        < config.LONDON_END
    )

    new_york = (
        config.NEW_YORK_START
        <= hour
        < config.NEW_YORK_END
    )

    return london or new_york


# =========================================================
# MARKET DISPLAY
# =========================================================

def print_market_info(
    price,
    pdh,
    pdl,
    asian_high,
    asian_low
):

    print()
    print("=" * 70)
    print("              ICT MARKET ANALYSIS V2")
    print("=" * 70)

    print(
        "UTC Time :",
        utc_now().strftime("%Y-%m-%d %H:%M:%S")
    )

    print(
        "Symbol   :",
        config.SYMBOL
    )

    print(
        "Price    :",
        price
    )

    print()

    print(
        "Previous Day High :",
        pdh
    )

    print(
        "Previous Day Low  :",
        pdl
    )

    print()

    print(
        "Asian High :",
        asian_high
    )

    print(
        "Asian Low  :",
        asian_low
    )

    print()

    print(
        "Kill Zone :",
        in_kill_zone()
    )

    print(
        "News Blackout :",
        config.NEWS_BLACKOUT
    )

    print("=" * 70)


# =========================================================
# DIAGNOSTIC
# =========================================================

def print_diagnostic(
    side,
    liquidity,
    displacement,
    mss,
    fvg,
    order_block,
    premium_discount,
    kill_zone,
    rr_ok
):

    score = calculate_ict_score(
        liquidity_sweep=liquidity,
        displacement=displacement,
        mss=mss,
        fvg=fvg,
        order_block=order_block,
        premium_discount=premium_discount,
        kill_zone=kill_zone,
        rr_ok=rr_ok
    )

    print()
    print("-" * 70)

    print(
        f"{side} ICT DIAGNOSTIC"
    )

    print("-" * 70)

    print(
        "Liquidity Sweep :",
        "YES" if liquidity else "NO"
    )

    print(
        "Displacement    :",
        "YES" if displacement else "NO"
    )

    print(
        "MSS / CHoCH     :",
        "YES" if mss else "NO"
    )

    print(
        "FVG             :",
        "YES" if fvg else "NO"
    )

    print(
        "Order Block     :",
        "YES" if order_block else "NO"
    )

    print(
        "Premium/Discount:",
        "YES" if premium_discount else "NO"
    )

    print(
        "Kill Zone       :",
        "YES" if kill_zone else "NO"
    )

    print(
        "R:R OK          :",
        "YES" if rr_ok else "NO"
    )

    print()

    print(
        "ICT SCORE       :",
        f"{score}/100"
    )

    if score >= 80:

        print(
            "QUALITY         : A+"
        )

    elif score >= 70:

        print(
            "QUALITY         : A"
        )

    elif score >= 60:

        print(
            "QUALITY         : B"
        )

    else:

        print(
            "QUALITY         : LOW"
        )

    print("-" * 70)


# =========================================================
# ANALYSIS
# =========================================================

def analyze_market():

    reset_daily_limits()

    print()
    print("Fetching fresh BTC market data...")

    if trades_today >= config.MAX_TRADES_PER_DAY:

        print(
            "Daily trade limit reached."
        )

        return None

    if daily_r <= -config.MAX_DAILY_LOSS_R:

        print(
            "Daily loss limit reached."
        )

        return None

    if (
        config.NEWS_FILTER_ENABLED
        and config.NEWS_BLACKOUT
    ):

        print(
            "NEWS BLACKOUT ACTIVE - NO TRADE"
        )

        return None

    # -----------------------------------------------------
    # FETCH DATA
    # -----------------------------------------------------

    htf = exchange.fetch_ohlcv(
        config.SYMBOL,
        config.HTF_TIMEFRAME,
        limit=config.HTF_LIMIT
    )

    ltf = exchange.fetch_ohlcv(
        config.SYMBOL,
        config.LTF_TIMEFRAME,
        limit=config.LTF_LIMIT
    )

    if len(htf) < 50:

        print(
            "Not enough HTF candles."
        )

        return None

    if len(ltf) < 50:

        print(
            "Not enough LTF candles."
        )

        return None

    # Remove currently forming candle

    htf_closed = htf[:-1]
    ltf_closed = ltf[:-1]

    price = ltf_closed[-1][4]

    # -----------------------------------------------------
    # KEY LEVELS
    # -----------------------------------------------------

    pdh, pdl = previous_day_high_low(
        htf_closed
    )

    asian_high, asian_low = asian_range(
        ltf_closed,
        config.ASIAN_START,
        config.ASIAN_END
    )

    print_market_info(
        price,
        pdh,
        pdl,
        asian_high,
        asian_low
    )

    kill_zone = in_kill_zone()

    # -----------------------------------------------------
    # RECENT SWINGS
    # -----------------------------------------------------

    recent_swing_high = find_recent_swing_high(
        ltf_closed
    )

    recent_swing_low = find_recent_swing_low(
        ltf_closed
    )

    # -----------------------------------------------------
    # LIQUIDITY LEVELS
    # -----------------------------------------------------

    bullish_levels = [
        ("Previous Day Low", pdl),
        ("Asian Low", asian_low),
        ("Recent Swing Low", recent_swing_low),
    ]

    bearish_levels = [
        ("Previous Day High", pdh),
        ("Asian High", asian_high),
        ("Recent Swing High", recent_swing_high),
    ]

    # =====================================================
    # BULLISH ANALYSIS
    # =====================================================

    bullish_sweep_found = False

    for level_name, level in bullish_levels:

        if level is None:
            continue

        if detect_liquidity_sweep(
            ltf_closed,
            level,
            "bullish"
        ):

            bullish_sweep_found = True

            print()
            print(
                "🟢 BULLISH LIQUIDITY SWEEP"
            )

            print(
                "Level :",
                level_name
            )

            print(
                "Price :",
                level
            )

            break

    bullish_displacement = detect_displacement(
        ltf_closed[-30:],
        "bullish",
        multiplier=config.DISPLACEMENT_MULTIPLIER
    )

    bullish_mss = detect_bullish_mss(
        ltf_closed
    )

    bullish_fvgs = find_bullish_fvgs(
        ltf_closed,
        lookback=50
    )

    bullish_fvg = (
        bullish_fvgs[-1]
        if bullish_fvgs
        else None
    )

    bullish_ob = find_bullish_order_block(
        ltf_closed,
        lookback=30
    )

    bullish_pd = False

    if recent_swing_high and recent_swing_low:

        bullish_pd = in_discount(
            price,
            recent_swing_high,
            recent_swing_low
        )

    print_diagnostic(
        "BULLISH",
        bullish_sweep_found,
        bullish_displacement,
        bullish_mss,
        bullish_fvg is not None,
        bullish_ob is not None,
        bullish_pd,
        kill_zone,
        False
    )

    # =====================================================
    # BEARISH ANALYSIS
    # =====================================================

    bearish_sweep_found = False

    for level_name, level in bearish_levels:

        if level is None:
            continue

        if detect_liquidity_sweep(
            ltf_closed,
            level,
            "bearish"
        ):

            bearish_sweep_found = True

            print()
            print(
                "🔴 BEARISH LIQUIDITY SWEEP"
            )

            print(
                "Level :",
                level_name
            )

            print(
                "Price :",
                level
            )

            break

    bearish_displacement = detect_displacement(
        ltf_closed[-30:],
        "bearish",
        multiplier=config.DISPLACEMENT_MULTIPLIER
    )

    bearish_mss = detect_bearish_mss(
        ltf_closed
    )

    bearish_fvgs = find_bearish_fvgs(
        ltf_closed,
        lookback=50
    )

    bearish_fvg = (
        bearish_fvgs[-1]
        if bearish_fvgs
        else None
    )

    bearish_ob = find_bearish_order_block(
        ltf_closed,
        lookback=30
    )

    bearish_pd = False

    if recent_swing_high and recent_swing_low:

        bearish_pd = in_premium(
            price,
            recent_swing_high,
            recent_swing_low
        )

    print_diagnostic(
        "BEARISH",
        bearish_sweep_found,
        bearish_displacement,
        bearish_mss,
        bearish_fvg is not None,
        bearish_ob is not None,
        bearish_pd,
        kill_zone,
        False
    )

    # =====================================================
    # ENTRY DECISION
    # =====================================================

    print()

    print(
        "CURRENT DECISION:"
    )

    # -----------------------------------------------------
    # LONG
    # -----------------------------------------------------

    if (
        bullish_sweep_found
        and bullish_displacement
        and bullish_mss
        and bullish_fvg is not None
        and bullish_pd
    ):

        entry = bullish_fvg["mid"]

        stop = min(
            bullish_fvg["low"],
            pdl if pdl else bullish_fvg["low"]
        ) * 0.999

        target = recent_swing_high

        if (
            target
            and target > entry
            and stop < entry
        ):

            risk = entry - stop

            reward = target - entry

            rr = reward / risk

            print(
                "Potential LONG R:R:",
                round(rr, 2)
            )

            if rr >= config.MIN_RR:

                print(
                    "🟢 VALID LONG SETUP"
                )

                return {
                    "side": "LONG",
                    "entry": entry,
                    "stop": stop,
                    "target": target,
                    "rr": rr,
                    "reason":
                        "Liquidity Sweep + "
                        "Displacement + "
                        "MSS + FVG + "
                        "Discount"
                }

    # -----------------------------------------------------
    # SHORT
    # -----------------------------------------------------

    if (
        bearish_sweep_found
        and bearish_displacement
        and bearish_mss
        and bearish_fvg is not None
        and bearish_pd
    ):

        entry = bearish_fvg["mid"]

        stop = max(
            bearish_fvg["high"],
            pdh if pdh else bearish_fvg["high"]
        ) * 1.001

        target = recent_swing_low

        if (
            target
            and target < entry
            and stop > entry
        ):

            risk = stop - entry

            reward = entry - target

            rr = reward / risk

            print(
                "Potential SHORT R:R:",
                round(rr, 2)
            )

            if rr >= config.MIN_RR:

                print(
                    "🔴 VALID SHORT SETUP"
                )

                return {
                    "side": "SHORT",
                    "entry": entry,
                    "stop": stop,
                    "target": target,
                    "rr": rr,
                    "reason":
                        "Liquidity Sweep + "
                        "Displacement + "
                        "MSS + FVG + "
                        "Premium"
                }

    print(
        "NO VALID ICT SETUP."
    )

    return None


# =========================================================
# SIGNAL
# =========================================================

def show_signal(signal):

    global last_signal_key
    global trades_today

    if signal is None:
        return

    key = (
        signal["side"],
        round(signal["entry"], 2),
        round(signal["stop"], 2),
        round(signal["target"], 2)
    )

    if key == last_signal_key:

        print(
            "Duplicate signal ignored."
        )

        return

    last_signal_key = key

    trades_today += 1

    print()
    print("=" * 70)
    print("🚨 ICT SIGNAL DETECTED")
    print("=" * 70)

    print(
        "SIDE   :",
        signal["side"]
    )

    print(
        "ENTRY  :",
        round(signal["entry"], 2)
    )

    print(
        "SL     :",
        round(signal["stop"], 2)
    )

    print(
        "TP     :",
        round(signal["target"], 2)
    )

    print(
        "R:R    :",
        round(signal["rr"], 2)
    )

    print(
        "REASON :",
        signal["reason"]
    )

    print("=" * 70)

    if config.PAPER_TRADING:

        print(
            "📝 PAPER TRADE ONLY"
        )

        print(
            "REAL ORDER WAS NOT SENT."
        )


# =========================================================
# MAIN
# =========================================================

def main():

    print()
    print("=" * 70)
    print("          ICT CRYPTO PAPER TRADING BOT V2")
    print("=" * 70)

    print(
        "Symbol :",
        config.SYMBOL
    )

    print(
        "HTF    :",
        config.HTF_TIMEFRAME
    )

    print(
        "LTF    :",
        config.LTF_TIMEFRAME
    )

    print(
        "Min RR :",
        config.MIN_RR
    )

    print(
        "Paper  :",
        config.PAPER_TRADING
    )

    print()
    print(
        "Market analysis: 24/7"
    )

    print(
        "Trading: Valid ICT setups, 24 hours"
    )

    while True:

        try:

            signal = analyze_market()

            show_signal(signal)

            print()
            print(
                "Waiting 60 seconds..."
            )

            time.sleep(60)

        except KeyboardInterrupt:

            print()
            print(
                "Bot stopped by user."
            )

            break

        except Exception as e:

            print()
            print(
                "ERROR:",
                type(e).__name__
            )

            print(e)

            print()
            print(
                "Retrying in 30 seconds..."
            )

            time.sleep(30)


if __name__ == "__main__":
    main()
