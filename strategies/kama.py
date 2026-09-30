# ============================================================
# KAMA + EFFICIENCY RATIO STRATEGY
# ============================================================

import pandas as pd


def _to_dataframe(candles):

    if candles is None:
        return None

    # Already a DataFrame
    if isinstance(candles, pd.DataFrame):
        df = candles.copy()

    # Raw CCXT OHLCV list
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
        df[column] = pd.to_numeric(
            df[column],
            errors="coerce",
        )

    df = df.dropna(
        subset=["close"]
    ).reset_index(drop=True)

    return df


# ============================================================
# EFFICIENCY RATIO
# ============================================================

def calculate_er(
    candles,
    period=10,
):

    df = _to_dataframe(candles)

    if df is None or len(df) < period + 1:
        return 0.0

    close = df["close"]

    change = abs(
        close.iloc[-1]
        - close.iloc[-1 - period]
    )

    volatility = (
        close.diff()
        .abs()
        .rolling(period)
        .sum()
        .iloc[-1]
    )

    if (
        pd.isna(volatility)
        or volatility == 0
    ):
        return 0.0

    er = change / volatility

    return float(
        max(0.0, min(er, 1.0))
    )


# ============================================================
# KAMA CALCULATION
# ============================================================

def calculate_kama(
    candles,
    period=10,
    fast=2,
    slow=30,
):

    df = _to_dataframe(candles)

    if df is None or len(df) < period + 1:
        return None

    close = df["close"]

    fast_sc = 2.0 / (fast + 1.0)
    slow_sc = 2.0 / (slow + 1.0)

    kama = [
        float(close.iloc[0])
    ]

    for i in range(1, len(close)):

        start = max(
            0,
            i - period,
        )

        change = abs(
            close.iloc[i]
            - close.iloc[start]
        )

        volatility = (
            close.iloc[start:i + 1]
            .diff()
            .abs()
            .sum()
        )

        if (
            pd.isna(volatility)
            or volatility == 0
        ):
            er = 0.0
        else:
            er = (
                change / volatility
            )

        er = max(
            0.0,
            min(float(er), 1.0),
        )

        sc = (
            er
            * (
                fast_sc
                - slow_sc
            )
            + slow_sc
        ) ** 2

        current_kama = (
            kama[-1]
            + sc
            * (
                close.iloc[i]
                - kama[-1]
            )
        )

        kama.append(
            current_kama
        )

    return float(kama[-1])


# ============================================================
# KAMA SIGNAL
# ============================================================

def kama_signal(
    candles,
    period=10,
    fast=2,
    slow=30,
):

    df = _to_dataframe(candles)

    if (
        df is None
        or len(df) < period + 5
    ):

        return {
            "side": "NEUTRAL",
            "score": 0,
            "reason": "Insufficient KAMA data",
        }

    close = float(
        df["close"].iloc[-1]
    )

    kama = calculate_kama(
        df,
        period,
        fast,
        slow,
    )

    previous_kama = calculate_kama(
        df.iloc[:-1],
        period,
        fast,
        slow,
    )

    if (
        kama is None
        or previous_kama is None
    ):

        return {
            "side": "NEUTRAL",
            "score": 0,
            "reason": "KAMA unavailable",
        }

    slope = (
        kama - previous_kama
    )

    er = calculate_er(
        df,
        period,
    )

    # --------------------------------------------------------
    # STRONG LONG
    # --------------------------------------------------------

    if (
        close > kama
        and slope > 0
    ):

        score = 85

        if er >= 0.60:
            score = 95

        return {
            "side": "LONG",
            "score": score,
            "reason": (
                f"Price above rising KAMA "
                f"ER={er:.2f}"
            ),
            "kama": kama,
            "er": er,
            "slope": slope,
        }

    # --------------------------------------------------------
    # STRONG SHORT
    # --------------------------------------------------------

    if (
        close < kama
        and slope < 0
    ):

        score = 85

        if er >= 0.60:
            score = 95

        return {
            "side": "SHORT",
            "score": score,
            "reason": (
                f"Price below falling KAMA "
                f"ER={er:.2f}"
            ),
            "kama": kama,
            "er": er,
            "slope": slope,
        }

    # --------------------------------------------------------
    # NO SIGNAL
    # --------------------------------------------------------

    return {
        "side": "NEUTRAL",
        "score": 0,
        "reason": (
            "KAMA direction not confirmed"
        ),
        "kama": kama,
        "er": er,
        "slope": slope,
    }