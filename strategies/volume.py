# =========================================================
# VOLUME STRATEGY
# Volume Spike + Price Confirmation
# =========================================================


def average_volume(candles, period=20):

    if len(candles) < period:
        return None

    volumes = [c[5] for c in candles[-period:]]

    return sum(volumes) / len(volumes)


def volume_signal(candles):
    """
    Volume Strategy.

    Uses:
    - Average volume
    - Volume spike
    - Candle direction

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

    current = candles[-1]

    open_price = current[1]
    close_price = current[4]
    current_volume = current[5]

    avg_volume = average_volume(candles[:-1], 20)

    if avg_volume is None or avg_volume <= 0:
        return {
            "side": "NEUTRAL",
            "score": 0,
            "reason": "Average volume unavailable"
        }

    volume_ratio = current_volume / avg_volume

    # -----------------------------------------------------
    # BULLISH VOLUME SPIKE
    # -----------------------------------------------------

    if volume_ratio >= 2.0 and close_price > open_price:

        return {
            "side": "LONG",
            "score": 90,
            "reason": f"Bullish volume spike: {volume_ratio:.2f}x average"
        }

    # -----------------------------------------------------
    # BEARISH VOLUME SPIKE
    # -----------------------------------------------------

    if volume_ratio >= 2.0 and close_price < open_price:

        return {
            "side": "SHORT",
            "score": 90,
            "reason": f"Bearish volume spike: {volume_ratio:.2f}x average"
        }

    # -----------------------------------------------------
    # MODERATE BULLISH VOLUME
    # -----------------------------------------------------

    if volume_ratio >= 1.5 and close_price > open_price:

        return {
            "side": "LONG",
            "score": 70,
            "reason": f"Above-average bullish volume: {volume_ratio:.2f}x"
        }

    # -----------------------------------------------------
    # MODERATE BEARISH VOLUME
    # -----------------------------------------------------

    if volume_ratio >= 1.5 and close_price < open_price:

        return {
            "side": "SHORT",
            "score": 70,
            "reason": f"Above-average bearish volume: {volume_ratio:.2f}x"
        }

    return {
        "side": "NEUTRAL",
        "score": 0,
        "reason": f"No significant volume confirmation: {volume_ratio:.2f}x"
    }
