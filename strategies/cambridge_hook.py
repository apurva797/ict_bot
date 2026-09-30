# ============================================================
# CAMBRIDGE HOOK STRATEGY
# ============================================================

import pandas as pd


# ============================================================
# CONVERT CCXT CANDLES -> DATAFRAME
# ============================================================

def _to_dataframe(candles):

    if candles is None:
        return None

    if isinstance(candles, pd.DataFrame):
        df = candles.copy()

    else:
        if len(candles) == 0:
            return None

        df = pd.DataFrame(
            candles,
            columns=[
                "timestamp",
                "open",
                "high",
                "low",
                "close",
                "volume",
            ],
        )

    for column in [
        "open",
        "high",
        "low",
        "close",
        "volume",
    ]:

        if column in df.columns:

            df[column] = pd.to_numeric(
                df[column],
                errors="coerce",
            )

    df = df.dropna(
        subset=[
            "open",
            "high",
            "low",
            "close",
            "volume",
        ]
    ).reset_index(drop=True)

    return df


# ============================================================
# RSI
# ============================================================

def calculate_rsi(
    close,
    period=14,
):

    delta = close.diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = (
        gain.rolling(period)
        .mean()
    )

    avg_loss = (
        loss.rolling(period)
        .mean()
    )

    rs = (
        avg_gain
        / avg_loss.replace(
            0,
            float("nan"),
        )
    )

    rsi = 100 - (
        100 / (1 + rs)
    )

    return rsi


# ============================================================
# CAMBRIDGE HOOK
# ============================================================

def cambridge_hook_signal(
    candles,
    require_oi=False,
):

    df = _to_dataframe(candles)

    if df is None or len(df) < 20:

        return {
            "side": "NEUTRAL",
            "score": 0,
            "reason": (
                "Insufficient Cambridge Hook data"
            ),
        }

    high = df["high"]
    low = df["low"]
    close = df["close"]
    volume = df["volume"]

    # --------------------------------------------------------
    # RSI
    # --------------------------------------------------------

    rsi = calculate_rsi(
        close,
        period=14,
    )

    current = -1
    previous = -2

    rsi_value = rsi.iloc[current]

    if pd.isna(rsi_value):

        return {
            "side": "NEUTRAL",
            "score": 0,
            "reason": "RSI unavailable",
        }

    # --------------------------------------------------------
    # OUTSIDE REVERSAL
    # --------------------------------------------------------

    outside_reversal = (
        high.iloc[current]
        > high.iloc[previous]
        and
        close.iloc[current]
        < low.iloc[previous]
    )

    if not outside_reversal:

        return {
            "side": "NEUTRAL",
            "score": 0,
            "reason": "No outside reversal",
        }

    # --------------------------------------------------------
    # RSI CONFIRMATION
    # --------------------------------------------------------

    if rsi_value <= 60:

        return {
            "side": "NEUTRAL",
            "score": 0,
            "reason": (
                f"Outside reversal but "
                f"RSI {rsi_value:.2f} <= 60"
            ),
        }

    # --------------------------------------------------------
    # VOLUME CONFIRMATION
    # --------------------------------------------------------

    volume_confirmed = (
        volume.iloc[current]
        > volume.iloc[previous]
    )

    if not volume_confirmed:

        return {
            "side": "NEUTRAL",
            "score": 0,
            "reason": (
                "Volume confirmation missing"
            ),
        }

    # --------------------------------------------------------
    # OPTIONAL OPEN INTEREST
    # --------------------------------------------------------

    oi_confirmed = False

    if "oi" in df.columns:

        oi = pd.to_numeric(
            df["oi"],
            errors="coerce",
        )

        current_oi = oi.iloc[current]
        previous_oi = oi.iloc[previous]

        if (
            not pd.isna(current_oi)
            and
            not pd.isna(previous_oi)
        ):

            oi_confirmed = (
                current_oi
                > previous_oi
            )

    if require_oi and not oi_confirmed:

        return {
            "side": "NEUTRAL",
            "score": 0,
            "reason": (
                "OI confirmation missing"
            ),
        }

    # --------------------------------------------------------
    # CONFIRMED CAMBRIDGE HOOK
    # --------------------------------------------------------

    reason = (
        "Cambridge Hook bearish reversal: "
        "outside reversal + RSI>60 + "
        "volume confirmation"
    )

    if oi_confirmed:
        reason += " + OI confirmation"

    return {
        "side": "SHORT",
        "score": 95,
        "reason": reason,
        "rsi": float(rsi_value),
        "oi_confirmed": oi_confirmed,
        "volume_confirmed": volume_confirmed,
        "reversal_high": float(
            high.iloc[current]
        ),
    }
