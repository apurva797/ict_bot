# =========================================================
# MULTI-MARKET / MULTI-STRATEGY SIGNAL SCORER
# =========================================================

BASE_WEIGHTS = {
    "ICT": 25,
    "TREND": 15,
    "BREAKOUT": 10,
    "VWAP": 10,
    "MOMENTUM": 10,
    "VOLATILITY": 5,
    "VOLUME": 10,
    "PRICE_ACTION": 5,
    "MEAN_REVERSION": 5,
    "WYCKOFF": 3,
    "CRYPTO": 2,

    # New strategies
    "KAMA": 10,
    "DONCHIAN": 10,
    "DIVERGENCE": 10,
    "CAMBRIDGE_HOOK": 10,
}


# =========================================================
# CONDITION CLASSIFICATION
# =========================================================

# Heavy strategies count as 2 confirmation points.
HEAVY_STRATEGIES = {
    "ICT",
    "CAMBRIDGE_HOOK",
    "DIVERGENCE",
    "DONCHIAN",
}

# Everything else with an active signal counts as 1.
NORMAL_STRATEGIES = {
    "TREND",
    "BREAKOUT",
    "VWAP",
    "MOMENTUM",
    "VOLATILITY",
    "VOLUME",
    "PRICE_ACTION",
    "MEAN_REVERSION",
    "WYCKOFF",
    "CRYPTO",
    "KAMA",
}


# =========================================================
# REGIME WEIGHTS
# =========================================================

def get_regime_weights(regime):

    weights = BASE_WEIGHTS.copy()

    if regime == "TREND_UP":

        weights["TREND"] += 10
        weights["BREAKOUT"] += 5
        weights["MOMENTUM"] += 5
        weights["DONCHIAN"] += 5
        weights["MEAN_REVERSION"] = 0

    elif regime == "TREND_DOWN":

        weights["TREND"] += 10
        weights["BREAKOUT"] += 5
        weights["MOMENTUM"] += 5
        weights["DONCHIAN"] += 5
        weights["MEAN_REVERSION"] = 0

    elif regime == "RANGE":

        weights["MEAN_REVERSION"] += 10
        weights["VWAP"] += 5
        weights["PRICE_ACTION"] += 5
        weights["MEAN_REVERSION"] += 5

        weights["BREAKOUT"] = 5
        weights["DONCHIAN"] = 5

    elif regime == "HIGH_VOLATILITY":

        weights["VOLATILITY"] += 10
        weights["MOMENTUM"] += 5
        weights["ICT"] += 5

    return weights


# =========================================================
# NORMALIZE SIGNAL
# =========================================================

def normalize_signal(signal):

    if not signal:

        return {
            "side": "NEUTRAL",
            "score": 0
        }

    side = signal.get("side", "NEUTRAL")

    try:
        score = float(signal.get("score", 0))
    except (TypeError, ValueError):

        score = 0

    score = max(0, min(score, 100))

    return {
        "side": side,
        "score": score
    }


# =========================================================
# CONDITION POINTS
# =========================================================

def get_condition_points(strategy_name, signal_score):

    if signal_score <= 0:
        return 0

    if strategy_name in HEAVY_STRATEGIES:

        return 2

    if strategy_name in NORMAL_STRATEGIES:

        return 1

    return 0


# =========================================================
# SCORE STRATEGIES
# =========================================================

def score_strategies(signals, regime="UNKNOWN"):

    weights = get_regime_weights(regime)

    long_score = 0
    short_score = 0

    long_weight = 0
    short_weight = 0

    active_long = []
    active_short = []

    long_conditions = []
    short_conditions = []

    long_heavy = []
    short_heavy = []

    conflicts = []

    # -----------------------------------------------------
    # PROCESS EVERY STRATEGY
    # -----------------------------------------------------

    for name, raw_signal in signals.items():

        signal = normalize_signal(raw_signal)

        side = signal["side"]
        score = signal["score"]

        weight = weights.get(name, 0)

        if weight <= 0:
            continue

        if side == "NEUTRAL" or score <= 0:
            continue

        # Confirmation points
        condition_points = get_condition_points(
            name,
            score
        )

        # -------------------------------------------------
        # LONG
        # -------------------------------------------------

        if side == "LONG":

            contribution = (score / 100) * weight

            long_score += contribution
            long_weight += weight

            active_long.append(name)

            if condition_points > 0:

                long_conditions.append({
                    "strategy": name,
                    "points": condition_points,
                    "score": score
                })

            if name in HEAVY_STRATEGIES:

                long_heavy.append(name)

        # -------------------------------------------------
        # SHORT
        # -------------------------------------------------

        elif side == "SHORT":

            contribution = (score / 100) * weight

            short_score += contribution
            short_weight += weight

            active_short.append(name)

            if condition_points > 0:

                short_conditions.append({
                    "strategy": name,
                    "points": condition_points,
                    "score": score
                })

            if name in HEAVY_STRATEGIES:

                short_heavy.append(name)

    # =====================================================
    # NORMALIZED WEIGHTED SCORES
    # =====================================================

    if long_weight > 0:
        final_long = (
            long_score / long_weight
        ) * 100
    else:
        final_long = 0

    if short_weight > 0:
        final_short = (
            short_score / short_weight
        ) * 100
    else:
        final_short = 0

    # =====================================================
    # CONFIRMATION POINTS
    # =====================================================

    long_points = sum(
        item["points"]
        for item in long_conditions
    )

    short_points = sum(
        item["points"]
        for item in short_conditions
    )

    # =====================================================
    # CONFLICT DETECTION
    # =====================================================

    if active_long and active_short:

        conflicts = list(
            set(active_long).intersection(active_short)
        )

        conflict_penalty = 10

        final_long -= conflict_penalty
        final_short -= conflict_penalty

    final_long = max(
        0,
        min(final_long, 100)
    )

    final_short = max(
        0,
        min(final_short, 100)
    )

    # =====================================================
    # DETERMINE DIRECTION
    # =====================================================

    if final_long == 0 and final_short == 0:

        return _no_trade_result(
            long_score=final_long,
            short_score=final_short,
            long_points=long_points,
            short_points=short_points,
            long_heavy=long_heavy,
            short_heavy=short_heavy,
            active_long=active_long,
            active_short=active_short,
            conflicts=conflicts
        )

    if final_long > final_short:

        side = "LONG"
        final_score = final_long

        confirmation_points = long_points
        heavy_conditions = long_heavy
        conditions = long_conditions

    elif final_short > final_long:

        side = "SHORT"
        final_score = final_short

        confirmation_points = short_points
        heavy_conditions = short_heavy
        conditions = short_conditions

    else:

        return _no_trade_result(
            long_score=final_long,
            short_score=final_short,
            long_points=long_points,
            short_points=short_points,
            long_heavy=long_heavy,
            short_heavy=short_heavy,
            active_long=active_long,
            active_short=active_short,
            conflicts=conflicts
        )

    # =====================================================
    # HARD CONFIRMATION RULE
    # =====================================================

    normal_confirmation = (
        confirmation_points >= 4
    )

    heavy_confirmation = (
        len(heavy_conditions) >= 2
    )

    confirmation_passed = (
        normal_confirmation
        or heavy_confirmation
    )

    # =====================================================
    # QUALITY
    # =====================================================

    if not confirmation_passed:

        quality = "NO TRADE"

    elif final_score >= 85:

        quality = "A+"

    elif final_score >= 75:

        quality = "A"

    elif final_score >= 65:

        quality = "B"

    elif final_score >= 55:

        quality = "C"

    else:

        quality = "NO TRADE"

    return {
        "side": side,
        "score": round(final_score, 2),

        "quality": quality,

        # Weighted scores
        "long_score": round(final_long, 2),
        "short_score": round(final_short, 2),

        # Confirmation system
        "confirmation_points": confirmation_points,
        "heavy_conditions_count": len(heavy_conditions),

        "confirmation_passed": confirmation_passed,

        "normal_confirmation": normal_confirmation,
        "heavy_confirmation": heavy_confirmation,

        "conditions": conditions,
        "heavy_conditions": heavy_conditions,

        # Strategy tracking
        "active_long": active_long,
        "active_short": active_short,

        "conflicts": conflicts
    }


# =========================================================
# NO TRADE RESULT
# =========================================================

def _no_trade_result(
    long_score,
    short_score,
    long_points,
    short_points,
    long_heavy,
    short_heavy,
    active_long,
    active_short,
    conflicts
):

    return {
        "side": "NEUTRAL",
        "score": 0,
        "quality": "NO TRADE",

        "long_score": round(long_score, 2),
        "short_score": round(short_score, 2),

        "long_confirmation_points": long_points,
        "short_confirmation_points": short_points,

        "long_heavy_conditions": len(long_heavy),
        "short_heavy_conditions": len(short_heavy),

        "confirmation_passed": False,

        "normal_confirmation": False,
        "heavy_confirmation": False,

        "conditions": [],
        "heavy_conditions": [],

        "active_long": active_long,
        "active_short": active_short,

        "conflicts": conflicts
    }


# =========================================================
# FINAL TRADE FILTER
# =========================================================

def should_trade(
    result,
    minimum_score=70
):

    if not result:
        return False

    if result.get("side") not in [
        "LONG",
        "SHORT"
    ]:
        return False

    # Hard confirmation rule
    if not result.get(
        "confirmation_passed",
        False
    ):
        return False

    # Weighted score filter
    if result.get("score", 0) < minimum_score:
        return False

    return True
