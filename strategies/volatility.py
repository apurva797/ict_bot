# =========================================================
# VOLATILITY STRATEGY
# Bollinger Bands + ATR
# =========================================================


def calculate_sma(values, period):
    if len(values) < period:
        return None

    return sum(values[-period:]) / period


def calculate_std(values, period):
    if len(values) < period:
        return None

    data = values[-period:]
    mean = sum(data) / period

    variance = sum((x - mean) ** 2 for x in data) / period

    return variance ** 0.5


def calculate_bollinger_bands(closes, period=20, multiplier=2):
    if len(closes) < period:
        return None

    middle = calculate_sma(closes, period)
    std = calculate_std(closes, period)

    if middle is None or std is None:
        return None

    upper = middle + multiplier * std
    lower = middle - multiplier * std

    return {
        "upper": upper,
        "middle": middle,
        "lower": lower
    }


def calculate_atr(candles, period=14):
    if len(candles) < period + 1:
        return None

    true_ranges = []

    for i in range(1, len(candles)):
        high = candles[i][2]
        low = candles[i][3]
        previous_close = candles[i - 1][4]

        tr1 = high - low
        tr2 = abs(high - previous_close)
        tr3 = abs(low - previous_close)

        true_range = max(tr1, tr2, tr3)

        true_ranges.append(true_range)

    if len(true_ranges) < period:
        return None

    return sum(true_ranges[-period:]) / period


def volatility_signal(candles):
    """
    Volatility Strategy:
    Bollinger Bands + ATR

    Returns:
        LONG / SHORT / NEUTRAL
        score: 0-100
    """

    if len(candles) < 30:
        return {
            "side": "NEUTRAL",
            "score": 0,
            "reason": "Not enough candles"
        }

    closes = [c[4] for c in candles]

    bands = calculate_bollinger_bands(closes)
    atr = calculate_atr(candles)

    if bands is None or atr is None:
        return {
            "side": "NEUTRAL",
            "score": 0,
            "reason": "Volatility indicators unavailable"
        }

    price = closes[-1]

    upper = bands["upper"]
    middle = bands["middle"]
    lower = bands["lower"]

    # Avoid division problems
    if middle == 0:
        return {
            "side": "NEUTRAL",
            "score": 0,
            "reason": "Invalid Bollinger Band calculation"
        }

    # Current candle range
    current_range = candles[-1][2] - candles[-1][3]

    # -----------------------------------------------------
    # BULLISH VOLATILITY BREAKOUT
    # -----------------------------------------------------

    if price > upper and current_range > atr:

        return {
            "side": "LONG",
            "score": 85,
            "reason": "Price broke above Bollinger upper band with ATR expansion"
        }

    # -----------------------------------------------------
    # BEARISH VOLATILITY BREAKOUT
    # -----------------------------------------------------

    if price < lower and current_range > atr:

        return {
            "side": "SHORT",
            "score": 85,
            "reason": "Price broke below Bollinger lower band with ATR expansion"
        }

    # -----------------------------------------------------
    # NO CLEAR VOLATILITY SIGNAL
    # -----------------------------------------------------

    return {
        "side": "NEUTRAL",
        "score": 0,
        "reason": "No confirmed Bollinger/ATR volatility signal"
    }