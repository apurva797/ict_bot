# ============================================================
# MOMENTUM DIVERGENCE STRATEGY
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
            "high",
            "low",
            "close",
        ]
    ).reset_index(drop=True)

    return df


# ============================================================
# STOCHASTIC %K
# ============================================================

def stochastic_k(
    candles,
    period=10,
):

    df = _to_dataframe(candles)

    if df is None or len(df) < period:
        return None

    lowest_low = (
        df["low"]
        .rolling(period)
        .min()
    )

    highest_high = (
        df["high"]
        .rolling(period)
        .max()
    )

    denominator = (
        highest_high
        - lowest_low
    )

    k = (
        (
            df["close"]
            - lowest_low
        )
        / denominator.replace(
            0,
            float("nan"),
        )
    ) * 100.0

    return k


# ============================================================
# SWING HIGHS
# ============================================================

def find_swing_highs(
    series,
    lookback=2,
):

    highs = []

    values = series.tolist()

    for i in range(
        lookback,
        len(values) - lookback,
    ):

        current = values[i]

        if pd.isna(current):
            continue

        left = values[
            i - lookback:i
        ]

        right = values[
            i + 1:i + lookback + 1
        ]

        if (
            all(
                current > x
                for x in left
                if not pd.isna(x)
            )
            and
            all(
                current >= x
                for x in right
                if not pd.isna(x)
            )
        ):

            highs.append(i)

    return highs


# ============================================================
# SWING LOWS
# ============================================================

def find_swing_lows(
    series,
    lookback=2,
):

    lows = []

    values = series.tolist()

    for i in range(
        lookback,
        len(values) - lookback,
    ):

        current = values[i]

        if pd.isna(current):
            continue

        left = values[
            i - lookback:i
        ]

        right = values[
            i + 1:i + lookback + 1
        ]

        if (
            all(
                current < x
                for x in left
                if not pd.isna(x)
            )
            and
            all(
                current <= x
                for x in right
                if not pd.isna(x)
            )
        ):

            lows.append(i)

    return lows


# ============================================================
# DIVERGENCE SIGNAL
# ============================================================

def divergence_signal(
    candles,
    period=10,
    swing_lookback=2,
    swing_threshold=0.03,
):

    df = _to_dataframe(candles)

    if df is None or len(df) < 30:

        return {
            "side": "NEUTRAL",
            "score": 0,
            "reason": (
                "Insufficient divergence data"
            ),
        }

    close = df["close"]

    stoch = stochastic_k(
        df,
        period,
    )

    if stoch is None:

        return {
            "side": "NEUTRAL",
            "score": 0,
            "reason": (
                "Stochastic unavailable"
            ),
        }

    # ========================================================
    # BEARISH DIVERGENCE
    #
    # Price makes higher high
    # Stochastic makes lower high
    # Minimum price difference = 3%
    # ========================================================

    price_highs = find_swing_highs(
        close,
        swing_lookback,
    )

    if len(price_highs) >= 2:

        first = price_highs[-2]
        second = price_highs[-1]

        price_1 = float(
            close.iloc[first]
        )

        price_2 = float(
            close.iloc[second]
        )

        indicator_1 = stoch.iloc[first]
        indicator_2 = stoch.iloc[second]

        if (
            not pd.isna(indicator_1)
            and
            not pd.isna(indicator_2)
        ):

            price_change = (
                price_2 - price_1
            ) / price_1

            if (
                price_change >= swing_threshold
                and
                price_2 > price_1
                and
                indicator_2 < indicator_1
            ):

                return {
                    "side": "SHORT",
                    "score": 95,
                    "reason": (
                        "Bearish divergence: "
                        "price higher high + "
                        "Stochastic lower high"
                    ),
                    "price_change": float(
                        price_change
                    ),
                    "indicator_previous": float(
                        indicator_1
                    ),
                    "indicator_current": float(
                        indicator_2
                    ),
                    "swing_type": "HIGH",
                }

    # ========================================================
    # BULLISH DIVERGENCE
    #
    # Price makes lower low
    # Stochastic makes higher low
    # Minimum price difference = 3%
    # ========================================================

    price_lows = find_swing_lows(
        close,
        swing_lookback,
    )

    if len(price_lows) >= 2:

        first = price_lows[-2]
        second = price_lows[-1]

        price_1 = float(
            close.iloc[first]
        )

        price_2 = float(
            close.iloc[second]
        )

        indicator_1 = stoch.iloc[first]
        indicator_2 = stoch.iloc[second]

        if (
            not pd.isna(indicator_1)
            and
            not pd.isna(indicator_2)
        ):

            price_change = (
                price_2 - price_1
            ) / price_1

            # For bullish divergence price_change
            # is negative, so compare its magnitude.
            if (
                price_change <= -swing_threshold
                and
                price_2 < price_1
                and
                indicator_2 > indicator_1
            ):

                return {
                    "side": "LONG",
                    "score": 95,
                    "reason": (
                        "Bullish divergence: "
                        "price lower low + "
                        "Stochastic higher low"
                    ),
                    "price_change": float(
                        price_change
                    ),
                    "indicator_previous": float(
                        indicator_1
                    ),
                    "indicator_current": float(
                        indicator_2
                    ),
                    "swing_type": "LOW",
                }

    # ========================================================
    # NO DIVERGENCE
    # ========================================================

    return {
        "side": "NEUTRAL",
        "score": 0,
        "reason": (
            "No confirmed momentum divergence"
        ),
    }
