"""
Data loading and validation pipeline for the Arjun crypto paper-trading app.
Handles ingestion, caching, sorting, deduplication, and open-candle filtering.
"""
from typing import Optional
import numpy as np
import pandas as pd
import streamlit st

def get_existing_exchange_data(symbol: str, timeframe: str) -> pd.DataFrame:
    try:
        df = pd.read_csv(f"ict_bot/sample_data/{symbol.replace('/', '_')}_{timeframe}.csv")
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        return df
    except Exception:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

@st.cache_data(ttl=300)
def load_and_clean_candles(symbol: str = "BTC/USDT", timeframe: str = "5m") -> Optional[pd.DataFrame]:
    df = get_existing_exchange_data(symbol, timeframe)
    if df.empty or len(df) < 2:
        return None
    df.columns = [col.lower() for col in df.columns]
    if "timestamp" not in df.columns:
        return None
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.sort_values("timestamp").reset_index(drop=True)
    df = df.drop_duplicates(subset=["timestamp"], keep="last").reset_index(drop=True)
    numeric_cols = ["open", "high", "low", "close", "volume"]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").astype(np.float64)
    df = df.dropna(subset=numeric_cols).reset_index(drop=True)
    if df.empty:
        return None
    df = df.iloc[:-1].reset_index(drop=True)
    return df

def validate_data_gaps(df: pd.DataFrame, expected_delta_minutes: int = 5) -> bool:
    if df.empty or len(df) < 2:
        return False
    time_diffs = df["timestamp"].diff().dropna()
    expected_delta = pd.Timedelta(minutes=expected_delta_minutes)
    large_gaps = time_diffs[time_diffs > (expected_delta * 2)]
    return len(large_gaps) == 0
