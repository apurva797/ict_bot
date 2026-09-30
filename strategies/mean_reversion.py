# =========================================================
# MEAN REVERSION STRATEGY
# Bollinger Bands + Distance From Mean
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


def mean_reversion_signal(candles):
    """
    Mean Reversion Strategy.

    Uses:
    - SMA 20
    - Standard deviation
    - Bollinger-style extremes

    Returns:
        LONG / SHORT / NEUTRAL
        score: 0-100
    """

    if len(candles) < 25:
        return {
            "side": "NEUTRAL",
            "score": 0,
            "reason": "Not enough candles"
        }

    closes = [c[4] for c in candles]

    period = 20

    mean = calculate_sma(closes, period)
    std = calculate_std(closes, period)

    if mean is None or std is None or std <= 0:
        return {
            "side": "NEUTRAL",
            "score": 0,
            "reason": "Mean calculation unavailable"
        }

    price = closes[-1]

    upper_extreme = mean + (2 * std)
    lower_extreme = mean - (2 * std)

    # -----------------------------------------------------
    # OVERSOLD / BELOW LOWER BAND
    # -----------------------------------------------------

    if price < lower_extreme:

        return {
            "side": "LONG",
            "score": 85,
            "reason": f"Price below mean-reversion extreme {lower_extreme:.2f}"
        }

    # -----------------------------------------------------
    # OVERBOUGHT / ABOVE UPPER BAND
    # -----------------------------------------------------

    if price > upper_extreme:

        return {
            "side": "SHORT",
            "score": 85,
            "reason": f"Price above mean-reversion extreme {upper_extreme:.2f}"
        }

    # -----------------------------------------------------
    # NO EXTREME
    # -----------------------------------------------------

    return {
        "side": "NEUTRAL",
        "score": 0,
        "reason": "Price is within normal mean range"
    }
