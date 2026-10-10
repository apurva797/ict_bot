import pandas as pd
from indicators import calculate_ema, calculate_rsi, calculate_atr

def extract_market_facts(df: pd.DataFrame, symbol: str, timeframe: str) -> dict:
    """Extracts immutable, audited data points to serve as ground truth for AI Copilot."""
    if len(df) < 50:
        raise ValueError("Insufficient data points (minimum 50 required).")

    latest = df.iloc[-1]
    
    close_price = float(latest['close'])
    ema_20 = float(calculate_ema(df['close'], 20).iloc[-1])
    ema_50 = float(calculate_ema(df['close'], 50).iloc[-1])
    rsi_14 = float(calculate_rsi(df['close'], 14).iloc[-1])
    atr_14 = float(calculate_atr(df, 14).iloc[-1])
    
    pct_change_1d = float(((close_price - df['close'].iloc[-24 if len(df) >= 24 else 0]) / df['close'].iloc[-24 if len(df) >= 24 else 0]) * 100)
    
    trend = "BULLISH" if close_price > ema_20 > ema_50 else ("BEARISH" if close_price < ema_20 < ema_50 else "NEUTRAL")
    
    return {
        "audit_meta": {
            "symbol": symbol,
            "timeframe": timeframe,
            "total_candles": len(df),
            "last_close_time": str(df.index[-1])
        },
        "price_metrics": {
            "close": close_price,
            "high": float(latest['high']),
            "low": float(latest['low']),
            "open": float(latest['open']),
            "volume": float(latest['volume']),
            "pct_change_24bar": round(pct_change_1d, 2)
        },
        "indicators": {
            "ema_20": round(ema_20, 2),
            "ema_50": round(ema_50, 2),
            "rsi_14": round(rsi_14, 2),
            "atr_14": round(atr_14, 2)
        },
        "computed_regime": {
            "trend": trend,
            "rsi_status": "OVERBOUGHT" if rsi_14 >= 70 else ("OVERSOLD" if rsi_14 <= 30 else "NEUTRAL")
        }
    }
