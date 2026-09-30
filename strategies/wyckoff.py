# =========================================================
# WYCKOFF STRATEGY
# Accumulation / Distribution + Volume
# =========================================================


def average_volume(candles, period=20):
    if len(candles) < period:
        return None

    volumes = [c[5] for c in candles[-period:]]

    return sum(volumes) / period


def average_range(candles, period=20):
    if len(candles) < period:
        return None

    ranges = [
        c[2] - c[3]
        for c in candles[-period:]
    ]

    return sum(ranges) / period


def wyckoff_signal(candles):
    """
    Simplified Wyckoff model.

    Looks for:
    - Accumulation-style behavior
    - Distribution-style behavior
    - Volume expansion
    - Breakout confirmation

    Returns:
        LONG / SHORT / NEUTRAL
        score: 0-100
    """

    if len(candles) < 60:
        return {
            "side": "NEUTRAL",
            "score": 0,
            "reason": "Not enough candles"
        }

    recent = candles[-20:]

    avg_vol = average_volume(candles[:-1], 20)
    avg_range = average_range(candles[:-1], 20)

    if avg_vol is None or avg_range is None or avg_range <= 0:
        return {
            "side": "NEUTRAL",
            "score": 0,
            "reason": "Wyckoff data unavailable"
        }

    current = candles[-1]

    open_price = current[1]
    high = current[2]
    low = current[3]
    close = current[4]
    volume = current[5]

    candle_range = high - low

    if candle_range <= 0:
        return {
            "side": "NEUTRAL",
            "score": 0,
            "reason": "Invalid candle range"
        }

    # Recent trading range
    range_high = max(c[2] for c in recent)
    range_low = min(c[3] for c in recent)

    range_size = range_high - range_low

    if range_size <= 0:
        return {
            "side": "NEUTRAL",
            "score": 0,
            "reason": "Invalid trading range"
        }

    position = (close - range_low) / range_size

    volume_ratio = volume / avg_vol

    # -----------------------------------------------------
    # ACCUMULATION / SPRING STYLE SIGNAL
    # -----------------------------------------------------

    spring = (
        low < range_low
        and close > range_low
        and close > open_price
    )

    if spring and volume_ratio >= 1.3:

        return {
            "side": "LONG",
            "score": 90,
            "reason": "Wyckoff spring-style liquidity rejection with volume"
        }

    # -----------------------------------------------------
    # DISTRIBUTION / UPTHRUST STYLE SIGNAL
    # -----------------------------------------------------

    upthrust = (
        high > range_high
        and close < range_high
        and close < open_price
    )

    if upthrust and volume_ratio >= 1.3:

        return {
            "side": "SHORT",
            "score": 90,
            "reason": "Wyckoff upthrust-style rejection with volume"
        }

    # -----------------------------------------------------
    # ACCUMULATION BREAKOUT
    # -----------------------------------------------------

    if (
        position > 0.85
        and close > open_price
        and volume_ratio >= 1.5
        and candle_range > avg_range
    ):

        return {
            "side": "LONG",
            "score": 80,
            "reason": "Strong upside breakout with volume expansion"
        }

    # -----------------------------------------------------
    # DISTRIBUTION BREAKDOWN
    # -----------------------------------------------------

    if (
        position < 0.15
        and close < open_price
        and volume_ratio >= 1.5
        and candle_range > avg_range
    ):

        return {
            "side": "SHORT",
            "score": 80,
            "reason": "Strong downside breakdown with volume expansion"
        }

    return {
        "side": "NEUTRAL",
        "score": 0,
        "reason": "No clear Wyckoff setup"
    }