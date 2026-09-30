# =========================================================
# VWAP STRATEGY
# =========================================================

from datetime import datetime, timezone


def calculate_session_vwap(candles):
    """
    Calculates VWAP for the current UTC trading day.
    """

    if not candles:
        return None

    today = datetime.now(timezone.utc).date()

    cumulative_pv = 0.0
    cumulative_volume = 0.0

    for candle in candles:

        timestamp = candle[0]
        high = candle[2]
        low = candle[3]
        close = candle[4]
        volume = candle[5]

        candle_date = datetime.fromtimestamp(
            timestamp / 1000,
            tz=timezone.utc
        ).date()

        if candle_date != today:
            continue

        typical_price = (high + low + close) / 3

        cumulative_pv += typical_price * volume
        cumulative_volume += volume

    if cumulative_volume == 0:
        return None

    return cumulative_pv / cumulative_volume


def vwap_signal(candles):
    """
    VWAP Strategy.

    Returns:
        LONG / SHORT / NEUTRAL
        score: 0-100
    """

    if len(candles) < 20:
        return {
            "side": "NEUTRAL",
            "score": 0,
            "reason": "Not enough candles"
        }

    vwap = calculate_session_vwap(candles)

    if vwap is None:
        return {
            "side": "NEUTRAL",
            "score": 0,
            "reason": "VWAP unavailable"
        }

    price = candles[-1][4]

    # Previous candle
    previous_price = candles[-2][4]

    # -----------------------------------------------------
    # BULLISH VWAP RECLAIM
    # -----------------------------------------------------

    if previous_price <= vwap and price > vwap:

        return {
            "side": "LONG",
            "score": 90,
            "reason": f"Price reclaimed VWAP at {vwap:.2f}"
        }

    # -----------------------------------------------------
    # BEARISH VWAP REJECTION
    # -----------------------------------------------------

    if previous_price >= vwap and price < vwap:

        return {
            "side": "SHORT",
            "score": 90,
            "reason": f"Price rejected VWAP at {vwap:.2f}"
        }

    # -----------------------------------------------------
    # PRICE ABOVE VWAP
    # -----------------------------------------------------

    if price > vwap:

        return {
            "side": "LONG",
            "score": 65,
            "reason": f"Price trading above VWAP {vwap:.2f}"
        }

    # -----------------------------------------------------
    # PRICE BELOW VWAP
    # -----------------------------------------------------

    if price < vwap:

        return {
            "side": "SHORT",
            "score": 65,
            "reason": f"Price trading below VWAP {vwap:.2f}"
        }

    return {
        "side": "NEUTRAL",
        "score": 0,
        "reason": "Price exactly at VWAP"
    }