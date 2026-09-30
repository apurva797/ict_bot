# =========================================================
# PRICE ACTION + SUPPORT / RESISTANCE STRATEGY
# =========================================================


def find_support(candles, lookback=50):

    if len(candles) < lookback:
        return None

    lows = [c[3] for c in candles[-lookback:]]

    return min(lows)


def find_resistance(candles, lookback=50):

    if len(candles) < lookback:
        return None

    highs = [c[2] for c in candles[-lookback:]]

    return max(highs)


def bullish_candle(candle):
    return candle[4] > candle[1]


def bearish_candle(candle):
    return candle[4] < candle[1]


def price_action_signal(candles):
    """
    Price Action + Support/Resistance.

    Detects:
    - Support rejection
    - Resistance rejection
    - Strong bullish/bearish candles

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

    open_price = current[1]
    high = current[2]
    low = current[3]
    close = current[4]

    support = find_support(candles[:-1], 50)
    resistance = find_resistance(candles[:-1], 50)

    if support is None or resistance is None:
        return {
            "side": "NEUTRAL",
            "score": 0,
            "reason": "Support/resistance unavailable"
        }

    candle_range = high - low

    if candle_range <= 0:
        return {
            "side": "NEUTRAL",
            "score": 0,
            "reason": "Invalid candle range"
        }

    body = abs(close - open_price)

    body_ratio = body / candle_range

    # -----------------------------------------------------
    # BULLISH SUPPORT REJECTION
    # -----------------------------------------------------

    touched_support = low <= support * 1.001

    bullish_close = close > open_price

    if touched_support and bullish_close:

        return {
            "side": "LONG",
            "score": 90,
            "reason": f"Bullish rejection from support {support:.2f}"
        }

    # -----------------------------------------------------
    # BEARISH RESISTANCE REJECTION
    # -----------------------------------------------------

    touched_resistance = high >= resistance * 0.999

    bearish_close = close < open_price

    if touched_resistance and bearish_close:

        return {
            "side": "SHORT",
            "score": 90,
            "reason": f"Bearish rejection from resistance {resistance:.2f}"
        }

    # -----------------------------------------------------
    # STRONG BULLISH CANDLE
    # -----------------------------------------------------

    if bullish_close and body_ratio >= 0.70:

        return {
            "side": "LONG",
            "score": 70,
            "reason": "Strong bullish price-action candle"
        }

    # -----------------------------------------------------
    # STRONG BEARISH CANDLE
    # -----------------------------------------------------

    if bearish_close and body_ratio >= 0.70:

        return {
            "side": "SHORT",
            "score": 70,
            "reason": "Strong bearish price-action candle"
        }

    return {
        "side": "NEUTRAL",
        "score": 0,
        "reason": "No strong price-action setup"
    }
