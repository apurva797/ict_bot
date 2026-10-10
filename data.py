import pandas as pd
from indicators import calculate_emaNiche diye gaye PowerShell commands ko copy karke direct apne **PowerShell terminal** mein paste karke **Enter** dabayein. Ye chaaron `.py` files aapke current directory (`ict_bot`) mein create kar dega:

```powershell
# 1. data.py
@'
import pandas as pd
import numpy as np
import logging

LOGGER = logging.getLogger("arjun_data")

def clean_ohlcv_dataframe(df: pd.DataFrame, exclude_open_candle: bool = True) -> pd.DataFrame:
    """Standardizes OHLCV DataFrame structure, cleans gaps, and strips unfinished candles."""
    if df.empty:
        raise ValueError("Provided DataFrame is empty.")
    
    frame = df.copy()
    
    column_mapping = {col: str(col).lower().strip() for col in frame.columns}
    frame.rename(columns=column_mapping, inplace=True)
    
    required_cols = {'open', 'high', 'low', 'close', 'volume'}
    if not required_cols.issubset(set(frame.columns)):
        raise ValueError(f"Missing required OHLCV columns. Found: {list(frame.columns)}")
    
    for col in list(required_cols):
        frame[col] = pd.to_numeric(frame[col], errors='coerce')
    
    frame.dropna(subset=['open', 'high', 'low', 'close'], inplace=True)
    
    if exclude_open_candle and len(frame) > 1:
        frame = frame.iloc[:-1]
        
    return frame.sort_index()
