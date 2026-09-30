def calculate_atr(candles, period=14):
    if len(candles) < period + 1:
        return None

    true_ranges = []

    for i in range(1, len(candles)):
        high = candles[i][2]
        low = candles[i][3]
        previous_close = candles[i - 1][4]

        tr = max(
            high - low,
            abs(high - previous_close),
            abs(low - previous_close)
        )

        true_ranges.append(tr)

    if len(true_ranges) < period:
        return None

    return sum(true_ranges[-period:]) / period


def calculate_atr_levels(
    entry,
    atr,
    side,
    sl_multiplier=1.5,
    rr=2.0
):
    if atr is None or atr <= 0:
        return None

    risk_distance = atr * sl_multiplier

    if side == "LONG":
        stop = entry - risk_distance
        target = entry + (risk_distance * rr)

    elif side == "SHORT":
        stop = entry + risk_distance
        target = entry - (risk_distance * rr)

    else:
        return None

    return {
        "entry": entry,
        "stop": stop,
        "target": target,
        "risk_distance": risk_distance,
        "rr": rr
    }


def calculate_rr(entry, stop, target, side):
    if side == "LONG":
        risk = entry - stop
        reward = target - entry

    elif side == "SHORT":
        risk = stop - entry
        reward = entry - target

    else:
        return 0.0

    if risk <= 0:
        return 0.0

    return reward / risk


def calculate_risk_amount(account_balance, risk_percent):
    if account_balance <= 0 or risk_percent <= 0:
        return 0.0

    return account_balance * (risk_percent / 100)


def calculate_position_size(
    account_balance,
    risk_percent,
    entry,
    stop,
    max_leverage=1.0
):
    if account_balance <= 0:
        return 0.0

    if entry <= 0 or stop <= 0:
        return 0.0

    risk_distance = abs(entry - stop)

    if risk_distance <= 0:
        return 0.0

    risk_amount = calculate_risk_amount(
        account_balance,
        risk_percent
    )

    quantity_from_risk = risk_amount / risk_distance

    max_notional = account_balance * max_leverage
    max_quantity = max_notional / entry

    quantity = min(
        quantity_from_risk,
        max_quantity
    )

    return quantity


def calculate_notional(entry, quantity):
    return entry * quantity


def calculate_fee(notional, fee_rate=0.0004):
    return notional * fee_rate


def calculate_round_trip_fees(
    entry,
    exit_price,
    quantity,
    fee_rate=0.0004
):
    entry_notional = entry * quantity
    exit_notional = exit_price * quantity

    entry_fee = calculate_fee(
        entry_notional,
        fee_rate
    )

    exit_fee = calculate_fee(
        exit_notional,
        fee_rate
    )

    return entry_fee + exit_fee


def calculate_net_pnl(
    entry,
    exit_price,
    quantity,
    side,
    fee_rate=0.0004
):
    if side == "LONG":
        gross_pnl = (exit_price - entry) * quantity

    elif side == "SHORT":
        gross_pnl = (entry - exit_price) * quantity

    else:
        return 0.0

    fees = calculate_round_trip_fees(
        entry,
        exit_price,
        quantity,
        fee_rate
    )

    return gross_pnl - fees


def validate_trade(
    entry,
    stop,
    target,
    side,
    min_rr=1.5
):
    rr = calculate_rr(
        entry,
        stop,
        target,
        side
    )

    if rr < min_rr:
        return False, rr, "R:R below minimum"

    if side == "LONG":

        if stop >= entry:
            return False, rr, "Invalid LONG stop"

        if target <= entry:
            return False, rr, "Invalid LONG target"

    elif side == "SHORT":

        if stop <= entry:
            return False, rr, "Invalid SHORT stop"

        if target >= entry:
            return False, rr, "Invalid SHORT target"

    else:
        return False, rr, "Invalid side"

    return True, rr, "VALID"


def check_daily_limits(
    daily_r,
    max_daily_loss_r=2.0
):
    if daily_r <= -abs(max_daily_loss_r):
        return False, "Daily loss limit reached"

    return True, "OK"
