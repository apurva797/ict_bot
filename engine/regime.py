def calculate_ema(values, period):
    if len(values) < period:
        return None

    multiplier = 2 / (period + 1)
    ema = sum(values[:period]) / period

    for price in values[period:]:
        ema = (price - ema) * multiplier + ema

    return ema


def detect_regime(candles):
    """
    Detects the current crypto market regime.

    Returns:
        TREND_UP
        TREND_DOWN
        RANGE
        HIGH_VOLATILITY
        UNKNOWN
    """

    if len(candles) < 200:
        return {
            "regime": "UNKNOWN",
            "score": 0,
            "reason": "Not enough candles"
        }

    closes = [c[4] for c in candles]
    highs = [c[2] for c in candles]
    lows = [c[3] for c in candles]

    ema20 = calculate_ema(closes, 20)
    ema50 = calculate_ema(closes, 50)
    ema200 = calculate_ema(closes, 200)

    if ema20 is None or ema50 is None or ema200 is None:
        return {
            "regime": "UNKNOWN",
            "score": 0,
            "reason": "EMA calculation unavailable"
        }

    price = closes[-1]

    # Recent volatility
    ranges = [
        highs[i] - lows[i]
        for i in range(max(0, len(candles) - 20), len(candles))
    ]

    average_range = sum(ranges) / len(ranges)
    current_range = highs[-1] - lows[-1]

    if average_range > 0 and current_range > average_range * 2.5:
        return {
            "regime": "HIGH_VOLATILITY",
            "score": 80,
            "reason": "Current candle range is unusually large"
        }

    # Bullish trend
    if price > ema20 and ema20 > ema50 and ema50 > ema200:
        return {
            "regime": "TREND_UP",
            "score": 90,
            "reason": "Price > EMA20 > EMA50 > EMA200"
        }

    # Bearish trend
    if price < ema20 and ema20 < ema50 and ema50 < ema200:
        return {
            "regime": "TREND_DOWN",
            "score": 90,
            "reason": "Price < EMA20 < EMA50 < EMA200"
        }

    # Range detection
    recent_high = max(highs[-50:])
    recent_low = min(lows[-50:])
    range_size = recent_high - recent_low

    if range_size > 0:
        position = (price - recent_low) / range_size

        if 0.2 < position < 0.8:
            return {
                "regime": "RANGE",
                "score": 70,
                "reason": "Price is trading inside recent range"
            }

    return {
        "regime": "RANGE",
        "score": 50,
        "reason": "No strong directional trend"
    }