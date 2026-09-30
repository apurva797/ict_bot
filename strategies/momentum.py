# =========================================================
# MOMENTUM STRATEGY
# RSI + MACD
# =========================================================


def calculate_ema(values, period):
    if len(values) < period:
        return None

    multiplier = 2 / (period + 1)

    ema = sum(values[:period]) / period

    for price in values[period:]:
        ema = (price - ema) * multiplier + ema

    return ema


def calculate_rsi(closes, period=14):
    if len(closes) < period + 1:
        return None

    gains = []
    losses = []

    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]

        if change > 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(change))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = ((avg_gain * (period - 1)) + gains[i]) / period
        avg_loss = ((avg_loss * (period - 1)) + losses[i]) / period

    if avg_loss == 0:
        return 100

    rs = avg_gain / avg_loss

    return 100 - (100 / (1 + rs))


def calculate_macd(closes):
    if len(closes) < 35:
        return None, None

    ema12 = calculate_ema(closes, 12)
    ema26 = calculate_ema(closes, 26)

    if ema12 is None or ema26 is None:
        return None, None

    macd = ema12 - ema26

    return macd, None


def momentum_signal(candles):
    """
    Momentum Strategy:
    RSI 14 + MACD 12/26

    Returns:
        LONG / SHORT / NEUTRAL
        score: 0-100
    """

    if len(candles) < 50:
        return {
            "side": "NEUTRAL",
            "score": 0,
            "reason": "Not enough candles"
        }

    closes = [c[4] for c in candles]

    rsi = calculate_rsi(closes)

    macd, _ = calculate_macd(closes)

    if rsi is None or macd is None:
        return {
            "side": "NEUTRAL",
            "score": 0,
            "reason": "Momentum indicators unavailable"
        }

    # MACD baseline
    macd_bullish = macd > 0
    macd_bearish = macd < 0

    # Strong bullish momentum
    if rsi > 55 and macd_bullish:
        score = 85

        if rsi > 60:
            score = 90

        return {
            "side": "LONG",
            "score": score,
            "reason": f"Bullish momentum: RSI {rsi:.1f}, MACD positive"
        }

    # Strong bearish momentum
    if rsi < 45 and macd_bearish:
        score = 85

        if rsi < 40:
            score = 90

        return {
            "side": "SHORT",
            "score": score,
            "reason": f"Bearish momentum: RSI {rsi:.1f}, MACD negative"
        }

    return {
        "side": "NEUTRAL",
        "score": 0,
        "reason": f"No strong momentum: RSI {rsi:.1f}"
    }
