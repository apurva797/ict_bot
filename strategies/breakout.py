# =========================================================
# BREAKOUT + RETEST STRATEGY
# =========================================================


def find_resistance(candles, lookback=50):
    if len(candles) < lookback:
        return None

    highs = [c[2] for c in candles[-lookback:]]

    return max(highs)


def find_support(candles, lookback=50):
    if len(candles) < lookback:
        return None

    lows = [c[3] for c in candles[-lookback:]]

    return min(lows)


def breakout_signal(candles):
    """
    Breakout + Retest Strategy.

    Returns:
        LONG / SHORT / NEUTRAL
        score: 0-100
    """

    if len(candles) < 55:
        return {
            "side": "NEUTRAL",
            "score": 0,
            "reason": "Not enough candles"
        }

    current = candles[-1]

    price = current[4]
    high = current[2]
    low = current[3]

    resistance = find_resistance(candles[:-1], 50)
    support = find_support(candles[:-1], 50)

    if resistance is None or support is None:
        return {
            "side": "NEUTRAL",
            "score": 0,
            "reason": "Support/resistance unavailable"
        }

    # -----------------------------------------------------
    # BULLISH BREAKOUT
    # -----------------------------------------------------

    if price > resistance:

        return {
            "side": "LONG",
            "score": 80,
            "reason": f"Bullish breakout above resistance {resistance:.2f}"
        }

    # -----------------------------------------------------
    # BEARISH BREAKOUT
    # -----------------------------------------------------

    if price < support:

        return {
            "side": "SHORT",
            "score": 80,
            "reason": f"Bearish breakout below support {support:.2f}"
        }

    # -----------------------------------------------------
    # RETEST AFTER BREAKOUT
    # -----------------------------------------------------

    previous = candles[-2]

    previous_close = previous[4]

    # Price touched resistance and is now holding above it
    if (
        previous_close > resistance
        and low <= resistance
        and price > resistance
    ):
        return {
            "side": "LONG",
            "score": 95,
            "reason": "Bullish breakout retest confirmed"
        }

    # Price touched support and is now holding below it
    if (
        previous_close < support
        and high >= support
        and price < support
    ):
        return {
            "side": "SHORT",
            "score": 95,
            "reason": "Bearish breakout retest confirmed"
        }

    return {
        "side": "NEUTRAL",
        "score": 0,
        "reason": "No confirmed breakout/retest"
    }
