import pandas as pd


def donchian_signal(candles, period=20):

    if candles is None or len(candles) < period + 2:
        return {
            "side": "NEUTRAL",
            "score": 0,
            "reason": "Not enough candles for Donchian"
        }

    try:
        df = pd.DataFrame(
            candles,
            columns=[
                "timestamp",
                "open",
                "high",
                "low",
                "close",
                "volume"
            ]
        )

        for column in [
            "open",
            "high",
            "low",
            "close",
            "volume"
        ]:
            df[column] = pd.to_numeric(
                df[column],
                errors="coerce"
            )

        df = df.dropna()

        df["donchian_high"] = (
            df["high"]
            .rolling(period)
            .max()
            .shift(1)
        )

        df["donchian_low"] = (
            df["low"]
            .rolling(period)
            .min()
            .shift(1)
        )

        current = df.iloc[-1]

        close = float(current["close"])
        upper = float(current["donchian_high"])
        lower = float(current["donchian_low"])

        if pd.isna(upper) or pd.isna(lower):
            return {
                "side": "NEUTRAL",
                "score": 0,
                "reason": "Donchian unavailable"
            }

        # Bullish breakout
        if close > upper:

            return {
                "side": "LONG",
                "score": 90,
                "reason": f"Donchian bullish breakout above {upper:.2f}",
                "donchian_high": upper,
                "donchian_low": lower
            }

        # Bearish breakout
        if close < lower:

            return {
                "side": "SHORT",
                "score": 90,
                "reason": f"Donchian bearish breakout below {lower:.2f}",
                "donchian_high": upper,
                "donchian_low": lower
            }

        return {
            "side": "NEUTRAL",
            "score": 0,
            "reason": f"Inside Donchian channel {lower:.2f}-{upper:.2f}",
            "donchian_high": upper,
            "donchian_low": lower
        }

    except Exception as e:

        return {
            "side": "NEUTRAL",
            "score": 0,
            "reason": f"Donchian error: {e}"
        }