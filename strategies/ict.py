# =========================================================
# ICT STRATEGY
# True Multi-Timeframe:
# HTF Bias (1H) + LTF Entry (5M)
# Liquidity Sweep + Displacement + MSS + FVG + OB
# =========================================================

from strategy import (
    detect_liquidity_sweep,
    detect_displacement,
    detect_bullish_mss,
    detect_bearish_mss,
    find_bullish_fvg,
    find_bearish_fvg,
    find_bullish_order_block,
    find_bearish_order_block,
)


# =========================================================
# SAFE HELPERS
# =========================================================

def _close(candle):
    return float(candle[4])


def _high(candle):
    return float(candle[2])


def _low(candle):
    return float(candle[3])


def _detect_htf_bias(htf_candles):
    """
    Determine broad directional bias from HTF candles.

    Uses simple market structure:
    - Higher highs + higher lows -> BULLISH
    - Lower highs + lower lows -> BEARISH
    - Otherwise -> NEUTRAL
    """

    if not htf_candles or len(htf_candles) < 20:
        return "NEUTRAL", "Not enough HTF candles"

    recent = htf_candles[-20:]

    mid = len(recent) // 2

    first_half = recent[:mid]
    second_half = recent[mid:]

    first_high = max(_high(c) for c in first_half)
    second_high = max(_high(c) for c in second_half)

    first_low = min(_low(c) for c in first_half)
    second_low = min(_low(c) for c in second_half)

    if second_high > first_high and second_low > first_low:
        return "BULLISH", "HTF bullish structure"

    if second_high < first_high and second_low < first_low:
        return "BEARISH", "HTF bearish structure"

    return "NEUTRAL", "HTF structure is mixed"


# =========================================================
# ICT SIGNAL
# =========================================================

def ict_signal(candles, htf_candles=None):
    """
    ICT multi-timeframe strategy.

    HTF:
        Determines broad directional bias.

    LTF:
        Finds actual entry setup.

    LTF conditions:
        - Liquidity sweep
        - Displacement
        - MSS
        - FVG
        - Order Block
        - FVG premium/discount

    Heavy strategy:
        ICT counts as ONE heavy confirmation in scorer.py.

    Returns:
        LONG / SHORT / NEUTRAL
        score: 0-100
    """

    # -----------------------------------------------------
    # BASIC VALIDATION
    # -----------------------------------------------------

    if candles is None or len(candles) < 100:
        return {
            "side": "NEUTRAL",
            "score": 0,
            "reason": "Not enough LTF candles"
        }

    # If HTF data is not supplied, preserve old behaviour.
    if htf_candles is None:
        htf_candles = candles

    price = _close(candles[-1])

    # -----------------------------------------------------
    # HTF BIAS
    # -----------------------------------------------------

    htf_bias, htf_reason = _detect_htf_bias(htf_candles)

    # -----------------------------------------------------
    # LTF LIQUIDITY LEVELS
    # -----------------------------------------------------

    recent_high = max(
        _high(c) for c in candles[-50:-1]
    )

    recent_low = min(
        _low(c) for c in candles[-50:-1]
    )

    # =====================================================
    # BULLISH LTF CONDITIONS
    # =====================================================

    bullish_sweep = detect_liquidity_sweep(
        candles,
        recent_low,
        "bullish"
    )

    bullish_displacement = detect_displacement(
        candles,
        "bullish"
    )

    bullish_mss = detect_bullish_mss(candles)

    bullish_fvg = find_bullish_fvg(candles)

    bullish_ob = find_bullish_order_block(candles)

    # =====================================================
    # BEARISH LTF CONDITIONS
    # =====================================================

    bearish_sweep = detect_liquidity_sweep(
        candles,
        recent_high,
        "bearish"
    )

    bearish_displacement = detect_displacement(
        candles,
        "bearish"
    )

    bearish_mss = detect_bearish_mss(candles)

    bearish_fvg = find_bearish_fvg(candles)

    bearish_ob = find_bearish_order_block(candles)

    # =====================================================
    # BULLISH SCORE
    # =====================================================

    long_score = 0
    long_reasons = []

    if bullish_sweep:
        long_score += 25
        long_reasons.append("LTF liquidity sweep")

    if bullish_displacement:
        long_score += 20
        long_reasons.append("Bullish displacement")

    if bullish_mss:
        long_score += 20
        long_reasons.append("Bullish MSS")

    if bullish_fvg is not None:
        long_score += 15
        long_reasons.append("Bullish FVG")

    if bullish_ob is not None:
        long_score += 10
        long_reasons.append("Bullish Order Block")

    if bullish_fvg is not None:
        fvg_mid = float(bullish_fvg["mid"])

        if price <= fvg_mid:
            long_score += 10
            long_reasons.append("FVG discount")

    # =====================================================
    # BEARISH SCORE
    # =====================================================

    short_score = 0
    short_reasons = []

    if bearish_sweep:
        short_score += 25
        short_reasons.append("LTF liquidity sweep")

    if bearish_displacement:
        short_score += 20
        short_reasons.append("Bearish displacement")

    if bearish_mss:
        short_score += 20
        short_reasons.append("Bearish MSS")

    if bearish_fvg is not None:
        short_score += 15
        short_reasons.append("Bearish FVG")

    if bearish_ob is not None:
        short_score += 10
        short_reasons.append("Bearish Order Block")

    if bearish_fvg is not None:
        fvg_mid = float(bearish_fvg["mid"])

        if price >= fvg_mid:
            short_score += 10
            short_reasons.append("FVG premium")

    # =====================================================
    # HTF DIRECTION FILTER
    # =====================================================

    # If HTF is bullish:
    # allow LONG normally
    # strongly reject SHORT

    if htf_bias == "BULLISH":

        if long_score >= 60:
            long_reasons.insert(
                0,
                "HTF bullish bias"
            )

        short_score = 0
        short_reasons = []

    # If HTF is bearish:
    # allow SHORT normally
    # strongly reject LONG

    elif htf_bias == "BEARISH":

        if short_score >= 60:
            short_reasons.insert(
                0,
                "HTF bearish bias"
            )

        long_score = 0
        long_reasons = []

    # If HTF is neutral:
    # both directions remain possible
    # but no HTF bonus is added.

    # =====================================================
    # FINAL DECISION
    # =====================================================

    if long_score > short_score and long_score >= 60:

        return {
            "side": "LONG",
            "score": min(long_score, 100),
            "reason": (
                f"{htf_reason}; "
                + ", ".join(long_reasons)
            )
        }

    if short_score > long_score and short_score >= 60:

        return {
            "side": "SHORT",
            "score": min(short_score, 100),
            "reason": (
                f"{htf_reason}; "
                + ", ".join(short_reasons)
            )
        }

    return {
        "side": "NEUTRAL",
        "score": max(long_score, short_score),
        "reason": (
            f"{htf_reason}; "
            "ICT conditions not sufficiently aligned"
        )
    }

