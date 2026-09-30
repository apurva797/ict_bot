# =========================================================
# TREND FOLLOWING STRATEGY
# =========================================================

def calculate_ema(values, period):
    if len(values) < period:
        return None

    multiplier = 2 / (period + 1)
    ema = sum(values[:period]) / period

    for price in values[period:]:
        ema = (price - ema) * multiplier + ema

    return ema


def trend_signal(candles):
    """
    Trend Following:
    EMA 20 / 50 / 200

    Returns:
        side  -> LONG / SHORT / NEUTRAL
        score -> 0-100
    """

    if len(candles) < 200:
        return {
            "side": "NEUTRAL",
            "score": 0,
            "reason": "Not enough candles"
        }

    closes = [c[4] for c in candles]

    ema20 = calculate_ema(closes, 20)
    ema50 = calculate_ema(closes, 50)
    ema200 = calculate_ema(closes, 200)

    if ema20 is None or ema50 is None or ema200 is None:
        return {
            "side": "NEUTRAL",
            "score": 0,
            "reason": "EMA calculation unavailable"
        }

    price = closes[-1]

    # Strong bullish trend
    if price > ema20 and ema20 > ema50 and ema50 > ema200:
        return {
            "side": "LONG",
            "score": 95,
            "reason": "Price > EMA20 > EMA50 > EMA200"
        }

    # Strong bearish trend
    if price < ema20 and ema20 < ema50 and ema50 < ema200:
        return {
            "side": "SHORT",
            "score": 95,
            "reason": "Price < EMA20 < EMA50 < EMA200"
        }

    # Moderate bullish trend
    if price > ema50 and ema50 > ema200:
        return {
            "side": "LONG",
            "score": 75,
            "reason": "Bullish medium/long-term trend"
        }

    # Moderate bearish trend
    if price < ema50 and ema50 < ema200:
        return {
            "side": "SHORT",
            "score": 75,
            "reason": "Bearish medium/long-term trend"
        }

    # No clear trend
    return {
        "side": "NEUTRAL",
        "score": 0,
        "reason": "No clear trend"
    }