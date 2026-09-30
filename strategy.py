from datetime import datetime, timezone


# =========================================================
# BASIC CANDLE FUNCTIONS
# =========================================================

def candle_body(c):
    return abs(c[4] - c[1])


def candle_range(c):
    return c[2] - c[3]


def is_bullish(c):
    return c[4] > c[1]


def is_bearish(c):
    return c[4] < c[1]


# =========================================================
# SWING STRUCTURE
# =========================================================

def is_swing_high(candles, i, strength=2):

    if i < strength or i > len(candles) - strength - 1:
        return False

    high = candles[i][2]

    for j in range(1, strength + 1):
        if high <= candles[i - j][2]:
            return False

        if high <= candles[i + j][2]:
            return False

    return True


def is_swing_low(candles, i, strength=2):

    if i < strength or i > len(candles) - strength - 1:
        return False

    low = candles[i][3]

    for j in range(1, strength + 1):
        if low >= candles[i - j][3]:
            return False

        if low >= candles[i + j][3]:
            return False

    return True


def find_swing_highs(candles, strength=2):

    return [
        candles[i][2]
        for i in range(len(candles))
        if is_swing_high(candles, i, strength)
    ]


def find_swing_lows(candles, strength=2):

    return [
        candles[i][3]
        for i in range(len(candles))
        if is_swing_low(candles, i, strength)
    ]


def find_recent_swing_high(candles, lookback=100):

    data = candles[-lookback:]

    swings = find_swing_highs(data)

    if not swings:
        return None

    return swings[-1]


def find_recent_swing_low(candles, lookback=100):

    data = candles[-lookback:]

    swings = find_swing_lows(data)

    if not swings:
        return None

    return swings[-1]


# =========================================================
# PREVIOUS DAY LEVELS
# =========================================================

def previous_day_high_low(candles):

    if len(candles) < 30:
        return None, None

    days = {}

    for c in candles:

        ts = datetime.fromtimestamp(
            c[0] / 1000,
            tz=timezone.utc
        ).date()

        days.setdefault(ts, []).append(c)

    dates = sorted(days.keys())

    if len(dates) < 2:
        return None, None

    previous_day = dates[-2]

    data = days[previous_day]

    pdh = max(c[2] for c in data)
    pdl = min(c[3] for c in data)

    return pdh, pdl


# =========================================================
# ASIAN RANGE
# =========================================================

def asian_range(candles, start_hour=0, end_hour=5):

    selected = []

    for c in candles:

        dt = datetime.fromtimestamp(
            c[0] / 1000,
            tz=timezone.utc
        )

        if start_hour <= dt.hour < end_hour:
            selected.append(c)

    if not selected:
        return None, None

    high = max(c[2] for c in selected)
    low = min(c[3] for c in selected)

    return high, low


# =========================================================
# LIQUIDITY SWEEP
# =========================================================

def detect_liquidity_sweep(
    candles,
    liquidity_level,
    direction
):

    if len(candles) < 2:
        return False

    if liquidity_level is None:
        return False

    c = candles[-1]

    # Sell-side liquidity taken
    if direction == "bullish":

        return (
            c[3] < liquidity_level
            and c[4] > liquidity_level
        )

    # Buy-side liquidity taken
    if direction == "bearish":

        return (
            c[2] > liquidity_level
            and c[4] < liquidity_level
        )

    return False


# =========================================================
# DISPLACEMENT
# =========================================================

def detect_displacement(
    candles,
    direction,
    lookback=20,
    multiplier=1.5
):

    if len(candles) < lookback + 1:
        return False

    current = candles[-1]

    previous = candles[-lookback - 1:-1]

    bodies = [
        candle_body(c)
        for c in previous
    ]

    avg_body = sum(bodies) / len(bodies)

    if avg_body == 0:
        return False

    current_body = candle_body(current)

    if current_body < avg_body * multiplier:
        return False

    if direction == "bullish":
        return is_bullish(current)

    if direction == "bearish":
        return is_bearish(current)

    return False


# =========================================================
# MSS / CHoCH
# =========================================================

def detect_bullish_mss(candles, lookback=30):

    if len(candles) < lookback:
        return False

    data = candles[-lookback:]

    swing_highs = find_swing_highs(data)

    if not swing_highs:
        return False

    last_swing_high = swing_highs[-1]

    return data[-1][4] > last_swing_high


def detect_bearish_mss(candles, lookback=30):

    if len(candles) < lookback:
        return False

    data = candles[-lookback:]

    swing_lows = find_swing_lows(data)

    if not swing_lows:
        return False

    last_swing_low = swing_lows[-1]

    return data[-1][4] < last_swing_low


# =========================================================
# FVG SEARCH
# =========================================================

def find_bullish_fvgs(candles, lookback=30):

    fvgs = []

    data = candles[-lookback:]

    for i in range(2, len(data)):

        c1 = data[i - 2]
        c2 = data[i - 1]
        c3 = data[i]

        if (
            c3[3] > c1[2]
            and is_bullish(c2)
        ):

            low = c1[2]
            high = c3[3]

            fvgs.append({
                "low": low,
                "high": high,
                "mid": (low + high) / 2,
                "index": i
            })

    return fvgs


def find_bearish_fvgs(candles, lookback=30):

    fvgs = []

    data = candles[-lookback:]

    for i in range(2, len(data)):

        c1 = data[i - 2]
        c2 = data[i - 1]
        c3 = data[i]

        if (
            c3[2] < c1[3]
            and is_bearish(c2)
        ):

            low = c3[2]
            high = c1[3]

            fvgs.append({
                "low": low,
                "high": high,
                "mid": (low + high) / 2,
                "index": i
            })

    return fvgs


def find_bullish_fvg(candles, lookback=30):

    fvgs = find_bullish_fvgs(
        candles,
        lookback
    )

    if not fvgs:
        return None

    return fvgs[-1]


def find_bearish_fvg(candles, lookback=30):

    fvgs = find_bearish_fvgs(
        candles,
        lookback
    )

    if not fvgs:
        return None

    return fvgs[-1]


def price_inside_fvg(price, fvg):

    if fvg is None:
        return False

    return (
        fvg["low"]
        <= price
        <= fvg["high"]
    )


# =========================================================
# FVG CONSEQUENT ENCROACHMENT
# =========================================================

def fvg_midpoint(fvg):

    if fvg is None:
        return None

    return (
        fvg["low"]
        + fvg["high"]
    ) / 2


# =========================================================
# PREMIUM / DISCOUNT
# =========================================================

def dealing_range(high, low):

    if high is None or low is None:
        return None

    if high <= low:
        return None

    midpoint = (high + low) / 2

    return {
        "high": high,
        "low": low,
        "equilibrium": midpoint,
        "premium": midpoint,
        "discount": midpoint
    }


def in_discount(price, high, low):

    if high is None or low is None:
        return False

    midpoint = (high + low) / 2

    return price < midpoint


def in_premium(price, high, low):

    if high is None or low is None:
        return False

    midpoint = (high + low) / 2

    return price > midpoint


# =========================================================
# EQUAL HIGHS / LOWS
# =========================================================

def find_equal_highs(
    candles,
    tolerance=0.0005,
    lookback=50
):

    data = candles[-lookback:]

    result = []

    for i in range(len(data)):

        for j in range(i + 1, len(data)):

            h1 = data[i][2]
            h2 = data[j][2]

            difference = abs(h1 - h2)

            if h1 == 0:
                continue

            if difference / h1 <= tolerance:

                result.append(
                    (h1 + h2) / 2
                )

    return result


def find_equal_lows(
    candles,
    tolerance=0.0005,
    lookback=50
):

    data = candles[-lookback:]

    result = []

    for i in range(len(data)):

        for j in range(i + 1, len(data)):

            l1 = data[i][3]
            l2 = data[j][3]

            difference = abs(l1 - l2)

            if l1 == 0:
                continue

            if difference / l1 <= tolerance:

                result.append(
                    (l1 + l2) / 2
                )

    return result


# =========================================================
# ORDER BLOCK
# =========================================================

def find_bullish_order_block(
    candles,
    lookback=20
):

    data = candles[-lookback:]

    for i in range(len(data) - 2, 0, -1):

        c = data[i]

        if is_bearish(c):

            return {
                "low": c[3],
                "high": c[2],
                "mid": (c[2] + c[3]) / 2
            }

    return None


def find_bearish_order_block(
    candles,
    lookback=20
):

    data = candles[-lookback:]

    for i in range(len(data) - 2, 0, -1):

        c = data[i]

        if is_bullish(c):

            return {
                "low": c[3],
                "high": c[2],
                "mid": (c[2] + c[3]) / 2
            }

    return None


# =========================================================
# OTE
# =========================================================

def bullish_ote(high, low):

    if high is None or low is None:
        return None

    if high <= low:
        return None

    range_size = high - low

    return {
        "62": high - range_size * 0.62,
        "70.5": high - range_size * 0.705,
        "79": high - range_size * 0.79
    }


def bearish_ote(high, low):

    if high is None or low is None:
        return None

    if high <= low:
        return None

    range_size = high - low

    return {
        "62": low + range_size * 0.62,
        "70.5": low + range_size * 0.705,
        "79": low + range_size * 0.79
    }


# =========================================================
# RISK / REWARD
# =========================================================

def calculate_rr(entry, stop, target):

    risk = abs(entry - stop)

    if risk == 0:
        return 0

    reward = abs(target - entry)

    return reward / risk


# =========================================================
# ICT SCORE
# =========================================================

def calculate_ict_score(
    liquidity_sweep=False,
    displacement=False,
    mss=False,
    fvg=False,
    order_block=False,
    premium_discount=False,
    kill_zone=False,
    rr_ok=False
):

    score = 0

    if liquidity_sweep:
        score += 20

    if displacement:
        score += 15

    if mss:
        score += 20

    if fvg:
        score += 15

    if order_block:
        score += 10

    if premium_discount:
        score += 10

    if kill_zone:
        score += 5

    if rr_ok:
        score += 5

    return score


# =========================================================
# SIGNAL BUILDERS
# =========================================================

def build_long_signal(
    entry,
    stop,
    target,
    reason,
    score=0
):

    rr = calculate_rr(
        entry,
        stop,
        target
    )

    return {
        "side": "LONG",
        "entry": entry,
        "stop": stop,
        "target": target,
        "rr": rr,
        "score": score,
        "reason": reason
    }


def build_short_signal(
    entry,
    stop,
    target,
    reason,
    score=0
):

    rr = calculate_rr(
        entry,
        stop,
        target
    )

    return {
        "side": "SHORT",
        "entry": entry,
        "stop": stop,
        "target": target,
        "rr": rr,
        "score": score,
        "reason": reason
    }
