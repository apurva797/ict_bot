"""
Extracts verifiable mathematical realities from candle time series.
Guarantees clean structures used to anchor LLM interpretations.
"""
from typing import Dict, List, Any
import numpy as np
import pandas as pd
from indicators import calculate_ema, calculate_max_drawdown

def extract_market_facts(df: pd.DataFrame) -> List[Dict[str, Any]]:
    facts =
    if df.empty or len(df) < 50:
        return [{"tag": "VERIFIED", "text": "Insufficient candle depth to parse quantitative realities safely.", "src": "system_validation"}]
    current_close = float(df["close"].iloc[-1])
    current_vol = float(df["volume"].iloc[-1])
    
    def get_horizon_return(periods: int, label: str) -> Dict[str, Any]:
        if len(df) >= periods:
            past_close = float(df["close"].iloc[-periods])
            ret = ((current_close - past_close) / past_close) * 100.0
            return {"tag": "VERIFIED", "text": f"BTC/USDT performance over past {label} is {ret:.2f}%.", "src": f"candles_{label}_close"}
        return {"tag": "VERIFIED", "text": f"Insufficient track for {label} return check.", "src": "candles_calc"}

    facts.append(get_horizon_return(12, "1h"))
    facts.append(get_horizon_return(48, "4h"))
    facts.append(get_horizon_return(288, "24h"))

    lookback_24h = min(288, len(df))
    window_24h = df.tail(lookback_24h)
    high_24h = float(window_24h["high"].max())
    low_24h = float(window_24h["low"].min())
    range_den = (high_24h - low_24h)
    pos_pct = ((current_close - low_24h) / range_den * 100.0) if range_den > 0 else 50.0
    facts.append({"tag": "VERIFIED", "text": f"Current price {current_close:.2f} lies at {pos_pct:.1f}% level of its 24h range [{low_24h:.2f} - {high_24h:.2f}].", "src": "range_24h_high_low"})

    log_ret = np.log(df["close"] / df["close"].shift(1))
    rolling_vol = log_ret.rolling(12).std()
    curr_vol_val = rolling_vol.iloc[-1]
    hist_sample = rolling_vol.dropna().tail(288 * 7)
    if not hist_sample.empty:
        vol_pct = (hist_sample < curr_vol_val).mean() * 100.0
        facts.append({"tag": "VERIFIED", "text": f"Short-term realized volatility metrics evaluate to the {vol_pct:.1f} percentile of a rolling 7-day historic backdrop.", "src": "volatility_7d_percentile"})

    ema20 = calculate_ema(df["close"], 20)
    ema50 = calculate_ema(df["close"], 50)
    curr_ema20 = float(ema20.iloc[-1])
    curr_ema50 = float(ema50.iloc[-1])
    trend_status = "BULLISH alignment" if curr_ema20 > curr_ema50 else "BEARISH alignment"
    facts.append({"tag": "VERIFIED", "text": f"Trend evaluation confirms a structural {trend_status} with EMA20 at {curr_ema20:.2f} relative to EMA50 at {curr_ema50:.2f}.", "src": "ema_20_50_crossover"})

    max_dd = calculate_max_drawdown(df["close"].tail(lookback_24h))
    facts.append({"tag": "VERIFIED", "text": f"Maximum calculated close-to-close peak-to-trough drawdown over the rolling 24h window is {max_dd:.2f}%.", "src": "drawdown_24h_calc"})

    avg_vol = float(df["volume"].tail(24).mean())
    vol_ratio = (current_vol / avg_vol) if avg_vol > 0 else 1.0
    facts.append({"tag": "VERIFIED", "text": f"Latest candle trading volume is {current_vol:.2f}, tracking at {vol_ratio:.2f}x of the trailing 2h baseline average volume [{avg_vol:.2f}].", "src": "volume_trailing_2h"})

    return facts
