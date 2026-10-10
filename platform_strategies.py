"""Shared, deterministic strategy plugin contract used by the Streamlit platform.

Plugins only produce signals. Existing backtest and paper-trading engines remain
responsible for fills, risk limits, fees, cooldowns, and position state.

The ``StrategyMetadata`` dataclass carries the full identity of a strategy:
family, variant, version, lifecycle status, risk profile, style, and regime
affinity.  The registry is the single source of truth for what can produce a
signal; every UI surface reads it rather than maintaining a parallel list.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

import pandas as pd

from demo_strategy import evaluate_conditions, indicators, validate_strategy


# ============================================================
# STRATEGY LIFECYCLE STATUS
# ============================================================

StrategyStatus = Literal[
    "research",       # Idea stage, no validated signal
    "backtested",     # Backtested but not yet validated OOS
    "validated",      # Passed walk-forward / OOS checks
    "paper_trading",  # Running in paper mode
    "experimental",   # Available but not yet robust
    "degraded",       # Performance deteriorating
    "retired",        # No longer recommended
]

StrategyHealth = Literal["healthy", "watch", "degraded", "retired"]


# ============================================================
# STRATEGY METADATA
# ============================================================

@dataclass(frozen=True)
class StrategyMetadata:
    """Complete identity of one strategy variant."""

    # Core identity
    id: str
    name: str
    description: str
    category: str

    # Family / variant hierarchy
    family: str = ""
    variant: str = ""
    version: str = "1.0.0"

    # Presentation
    style: str = ""                                    # e.g. "Intraday", "Swing", "Scalping"
    risk_profile: str = "moderate"                     # conservative / moderate / aggressive
    icon: str = ""                                     # emoji or short label

    # Capability
    supported_markets: tuple[str, ...] = ("crypto spot",)
    supported_timeframes: tuple[str, ...] = ("5m", "15m", "1h")
    min_candles: int = 35

    # Lifecycle
    status: StrategyStatus = "experimental"
    health: StrategyHealth = "healthy"

    # Research
    best_regime: str = ""                              # e.g. "Trending", "Ranging"
    worst_regime: str = ""
    entry_logic: str = ""
    exit_logic: str = ""

    # Optional tags for filtering
    tags: tuple[str, ...] = ()


# ============================================================
# STRATEGY SIGNAL
# ============================================================

@dataclass(frozen=True)
class StrategySignal:
    side: str
    timestamp: Any
    explanation: str
    features: dict[str, float]


# ============================================================
# PLUGIN PROTOCOL
# ============================================================

class StrategyPlugin(Protocol):
    metadata: StrategyMetadata

    def signal_series(self, frame: pd.DataFrame, parameters: dict[str, Any] | None = None) -> pd.Series:
        """Return one of BUY, SELL, or None per candle; never access future rows."""


# ============================================================
# STRATEGY REGISTRY
# ============================================================

class StrategyRegistry:
    """In-process registry; registering a duplicate id is an error."""

    def __init__(self):
        self._plugins: dict[str, StrategyPlugin] = {}

    def register(self, plugin: StrategyPlugin) -> None:
        if not plugin.metadata.id or plugin.metadata.id in self._plugins:
            raise ValueError(f"Strategy id is empty or already registered: {plugin.metadata.id!r}")
        self._plugins[plugin.metadata.id] = plugin

    def get(self, strategy_id: str) -> StrategyPlugin:
        try:
            return self._plugins[strategy_id]
        except KeyError as exc:
            raise KeyError(f"Unknown strategy: {strategy_id}") from exc

    def list(self) -> tuple[StrategyMetadata, ...]:
        return tuple(plugin.metadata for plugin in self._plugins.values())

    def families(self) -> dict[str, list[StrategyMetadata]]:
        """Group strategies by family for the Strategy Library."""
        groups: dict[str, list[StrategyMetadata]] = {}
        for plugin in self._plugins.values():
            family = plugin.metadata.family or plugin.metadata.category
            groups.setdefault(family, []).append(plugin.metadata)
        return groups

    def by_status(self, status: StrategyStatus) -> tuple[StrategyMetadata, ...]:
        return tuple(p.metadata for p in self._plugins.values()
                     if p.metadata.status == status)

    def selectable(self) -> tuple[StrategyMetadata, ...]:
        """Strategies that can actually produce a signal and be traded."""
        return tuple(p.metadata for p in self._plugins.values()
                     if p.metadata.status not in ("retired",))


# ============================================================
# BUILT-IN PLUGINS
# ============================================================

class IctStrategyPlugin:
    metadata = StrategyMetadata(
        id="ict", name="ARJUNA", category="ICT / Market Structure",
        family="Arjun", variant="Combined ICT Setup",
        description="Liquidity sweep, displacement, MSS, FVG, order block, and HTF-bias signal logic.",
        style="Intraday", risk_profile="moderate", icon="🏛️",
        min_candles=100, status="validated", health="healthy",
        best_regime="Trending", worst_regime="Low Volatility Ranging",
        entry_logic="Liquidity sweep → displacement → MSS → FVG/OB entry in discount/premium zone",
        exit_logic="Fixed R:R target (default 2R) or stop loss below/above structure",
        tags=("ict", "smart-money", "institutional"),
    )

    def signal_series(self, frame, parameters=None):
        from strategies.ict import ict_signal

        signals = pd.Series(None, index=frame.index, dtype="object")
        all_candles = [
            [int(ts.timestamp() * 1000), float(row.open), float(row.high), float(row.low), float(row.close), float(row.volume)]
            for ts, row in frame.iterrows()
        ]
        for end in range(self.metadata.min_candles, len(frame) + 1):
            candles = all_candles[:end]
            result = ict_signal(candles)
            side = result.get("side") if isinstance(result, dict) else None
            if side in {"LONG", "SHORT"}:
                signals.iloc[end - 1] = "BUY" if side == "LONG" else "SELL"
        return signals


class QuantStrategyPlugin:
    """A parameterized indicator-rule adapter; variants share the same engine."""

    def __init__(self, strategy_id, name, description, entry, exit_, min_candles=35,
                 family="Quant", variant="", style="Intraday", risk_profile="moderate",
                 status="validated", best_regime="", worst_regime="",
                 entry_logic="", exit_logic="", tags=()):
        self.metadata = StrategyMetadata(
            id=strategy_id, name=name, description=description, category="Quant",
            family=family, variant=variant, version="1.0.0",
            style=style, risk_profile=risk_profile, icon="📊",
            min_candles=min_candles, status=status,
            best_regime=best_regime, worst_regime=worst_regime,
            entry_logic=entry_logic, exit_logic=exit_logic,
            tags=tags,
        )
        self._entry = entry
        self._exit = exit_

    def signal_series(self, frame, parameters=None):
        parameters = parameters or {}
        entry, exit_ = self._entry(parameters), self._exit(parameters)
        entry_mask = evaluate_conditions(frame, entry).fillna(False)
        exit_mask = evaluate_conditions(frame, exit_).fillna(False)
        side = parameters.get("side", "BUY")
        signals = pd.Series(None, index=frame.index, dtype="object")
        signals.loc[entry_mask] = side
        # Opposite-side signals are used by the shared engine as an exit or reversal.
        signals.loc[exit_mask] = "SELL" if side == "BUY" else "BUY"
        return signals


class CandleSignalPlugin:
    """Adapter for the existing strategies/*.py signal functions.

    Every strategy module in the ``strategies`` package follows the same
    contract: ``signal_function(candles) -> {side, score, reason}``.  This
    adapter bridges that contract to the ``StrategyPlugin`` protocol expected
    by the registry, backtester, and paper engine.
    """

    def __init__(self, metadata: StrategyMetadata, signal_fn_path: str):
        self.metadata = metadata
        self._signal_fn_path = signal_fn_path
        self._signal_fn = None

    def _load(self):
        if self._signal_fn is None:
            module_name, fn_name = self._signal_fn_path.rsplit(".", 1)
            import importlib
            mod = importlib.import_module(module_name)
            self._signal_fn = getattr(mod, fn_name)
        return self._signal_fn

    def signal_series(self, frame, parameters=None):
        signal_fn = self._load()
        signals = pd.Series(None, index=frame.index, dtype="object")
        min_c = self.metadata.min_candles
        all_candles = [
            [int(ts.timestamp() * 1000), float(row.open), float(row.high),
             float(row.low), float(row.close), float(row.volume)]
            for ts, row in frame.iterrows()
        ]
        for end in range(min_c, len(frame) + 1):
            candles = all_candles[:end]
            result = signal_fn(candles)
            side = result.get("side") if isinstance(result, dict) else None
            if side in {"LONG", "SHORT"}:
                signals.iloc[end - 1] = "BUY" if side == "LONG" else "SELL"
        return signals


class CustomDslStrategyPlugin:
    metadata = StrategyMetadata(
        id="custom.dsl", name="Custom Strategy", category="Custom",
        family="Custom", variant="User-defined rules",
        description="Validated, deterministic strategy rules from the restricted strategy DSL.",
        style="User-defined", risk_profile="variable", icon="✏️",
        status="experimental",
        entry_logic="User-defined entry conditions via DSL or natural language",
        exit_logic="User-defined exit or default R:R target",
        tags=("custom", "user-defined"),
    )

    def signal_series(self, frame, parameters=None):
        spec = validate_strategy(parameters or {})
        signals = pd.Series(None, index=frame.index, dtype="object")
        entry_mask = evaluate_conditions(frame, spec["entry"]).fillna(False)
        signals.loc[entry_mask] = spec["side"]
        return signals


# ============================================================
# QUANT HELPERS (preserved from original)
# ============================================================

def _condition(indicator, operator, value=None, period=None, compare_to=None):
    item = {"indicator": indicator, "operator": operator}
    if period is not None:
        item["period"] = period
    if compare_to is not None:
        item["compare_to"] = compare_to
    else:
        item["value"] = value
    return item


def _ema_cross_entry(parameters):
    fast, slow = int(parameters.get("fast", 20)), int(parameters.get("slow", 50))
    return [_condition("EMA", "crosses_above", period=fast, compare_to={"indicator": "EMA", "period": slow})]


def _ema_cross_exit(parameters):
    fast, slow = int(parameters.get("fast", 20)), int(parameters.get("slow", 50))
    return [_condition("EMA", "crosses_below", period=fast, compare_to={"indicator": "EMA", "period": slow})]


def _rsi_entry(parameters):
    period = int(parameters.get("period", 14))
    threshold = float(parameters.get("oversold", 30))
    return [_condition("RSI", "<", threshold, period)]


def _rsi_exit(parameters):
    period = int(parameters.get("period", 14))
    threshold = float(parameters.get("overbought", 70))
    return [_condition("RSI", ">", threshold, period)]


# ============================================================
# SIGNAL HELPER
# ============================================================

def latest_signal(plugin: StrategyPlugin, frame: pd.DataFrame, parameters=None) -> StrategySignal | None:
    if frame is None or len(frame) < plugin.metadata.min_candles:
        return None
    values = plugin.signal_series(frame, parameters)
    side = values.iloc[-1]
    if side not in {"BUY", "SELL"}:
        return None
    return StrategySignal(
        side=side, timestamp=frame.index[-1], explanation=f"{plugin.metadata.name} rules produced a {side} signal.",
        features={"close": float(frame.close.iloc[-1])},
    )


# ============================================================
# BUILD REGISTRY
# ============================================================

strategy_registry = StrategyRegistry()

# ---- Arjun / ICT ----
strategy_registry.register(IctStrategyPlugin())

# ---- Quant (parameterized indicator rules) ----
strategy_registry.register(QuantStrategyPlugin(
    "quant.trend", "EMA Trend Following", "Configurable fast/slow EMA crossover.",
    _ema_cross_entry, _ema_cross_exit,
    family="Quant", variant="EMA Crossover", style="Swing",
    best_regime="Trending", worst_regime="Ranging",
    entry_logic="Fast EMA crosses above slow EMA",
    exit_logic="Fast EMA crosses below slow EMA",
    tags=("trend", "ema", "crossover"),
))
strategy_registry.register(QuantStrategyPlugin(
    "quant.mean_reversion", "RSI Mean Reversion", "Configurable oversold/overbought RSI rules.",
    _rsi_entry, _rsi_exit, min_candles=35,
    family="Quant", variant="RSI Extreme", style="Intraday",
    best_regime="Ranging", worst_regime="Strong Trending",
    entry_logic="RSI drops below oversold threshold (default 30)",
    exit_logic="RSI rises above overbought threshold (default 70)",
    tags=("mean-reversion", "rsi", "oscillator"),
))

# ---- Price Action family ----
strategy_registry.register(CandleSignalPlugin(
    StrategyMetadata(
        id="price_action", name="Price Action", category="Price Action",
        family="Price Action", variant="Support/Resistance Rejection",
        description="Support/resistance rejection and strong candle patterns.",
        style="Intraday", risk_profile="moderate", icon="📐",
        min_candles=55, status="backtested",
        best_regime="Ranging", worst_regime="Low Volatility",
        entry_logic="Bullish rejection from support or bearish rejection from resistance, "
                    "or strong directional candle with body ratio ≥ 70%",
        exit_logic="Fixed R:R target",
        tags=("price-action", "support-resistance", "rejection"),
    ),
    "strategies.price_action.price_action_signal",
))

# ---- Breakout family ----
strategy_registry.register(CandleSignalPlugin(
    StrategyMetadata(
        id="breakout", name="Breakout & Retest", category="Breakout",
        family="Breakout", variant="Break & Retest",
        description="Breakout above resistance / below support with retest confirmation.",
        style="Intraday", risk_profile="moderate", icon="🚀",
        min_candles=55, status="backtested",
        best_regime="Trending", worst_regime="Choppy / Ranging",
        entry_logic="Price breaks and retests resistance (long) or support (short)",
        exit_logic="Fixed R:R target",
        tags=("breakout", "retest", "structure"),
    ),
    "strategies.breakout.breakout_signal",
))

# ---- Trend Following family ----
strategy_registry.register(CandleSignalPlugin(
    StrategyMetadata(
        id="trend", name="EMA Trend", category="Trend Following",
        family="Trend Following", variant="Triple EMA",
        description="Triple EMA (20/50/200) trend alignment.",
        style="Swing", risk_profile="moderate", icon="📈",
        min_candles=200, status="backtested",
        best_regime="Strong Trending", worst_regime="Ranging / Choppy",
        entry_logic="Price > EMA20 > EMA50 > EMA200 (long) or inverse (short)",
        exit_logic="Trend alignment broken",
        tags=("trend", "ema", "multi-timeframe"),
    ),
    "strategies.trend.trend_signal",
))

# ---- Momentum family ----
strategy_registry.register(CandleSignalPlugin(
    StrategyMetadata(
        id="momentum", name="RSI + MACD Momentum", category="Momentum",
        family="Momentum", variant="RSI MACD",
        description="Combined RSI and MACD momentum confirmation.",
        style="Intraday", risk_profile="moderate", icon="⚡",
        min_candles=50, status="backtested",
        best_regime="Trending", worst_regime="Ranging / Low Volatility",
        entry_logic="RSI > 55 + MACD positive (long) or RSI < 45 + MACD negative (short)",
        exit_logic="Momentum reversal or fixed target",
        tags=("momentum", "rsi", "macd"),
    ),
    "strategies.momentum.momentum_signal",
))

# ---- Mean Reversion family ----
strategy_registry.register(CandleSignalPlugin(
    StrategyMetadata(
        id="mean_reversion", name="Bollinger Reversion", category="Mean Reversion",
        family="Mean Reversion", variant="Bollinger Bands",
        description="Bollinger Band distance-from-mean reversion strategy.",
        style="Intraday", risk_profile="conservative", icon="🔄",
        min_candles=30, status="backtested",
        best_regime="Ranging", worst_regime="Strong Trending",
        entry_logic="Price touches or breaches lower Bollinger Band (long) or upper (short)",
        exit_logic="Price returns to SMA (mean)",
        tags=("mean-reversion", "bollinger", "statistical"),
    ),
    "strategies.mean_reversion.mean_reversion_signal",
))

# ---- Volatility family ----
strategy_registry.register(CandleSignalPlugin(
    StrategyMetadata(
        id="volatility", name="Bollinger + ATR", category="Volatility",
        family="Volatility", variant="Bollinger ATR",
        description="Bollinger Bands squeeze and ATR breakout detection.",
        style="Intraday", risk_profile="aggressive", icon="🌊",
        min_candles=30, status="backtested",
        best_regime="High Volatility", worst_regime="Low Volatility Compression",
        entry_logic="Price breaks Bollinger Band during ATR expansion",
        exit_logic="Volatility contraction or fixed target",
        tags=("volatility", "bollinger", "atr", "squeeze"),
    ),
    "strategies.volatility.volatility_signal",
))

# ---- VWAP family ----
strategy_registry.register(CandleSignalPlugin(
    StrategyMetadata(
        id="vwap", name="VWAP Strategy", category="Volume",
        family="Volume", variant="VWAP Reversion",
        description="VWAP deviation-based entries with volume confirmation.",
        style="Intraday", risk_profile="moderate", icon="📊",
        min_candles=30, status="backtested",
        best_regime="Ranging / Normal Volatility", worst_regime="Strong Trending",
        entry_logic="Price deviates significantly from VWAP with volume confirmation",
        exit_logic="Price returns toward VWAP",
        tags=("vwap", "volume", "intraday"),
    ),
    "strategies.vwap.vwap_signal",
))

# ---- Volume family ----
strategy_registry.register(CandleSignalPlugin(
    StrategyMetadata(
        id="volume", name="Volume Profile", category="Volume",
        family="Volume", variant="Volume Spike",
        description="Volume-based signal detection with relative volume analysis.",
        style="Intraday", risk_profile="moderate", icon="📶",
        min_candles=30, status="experimental",
        best_regime="High Volume", worst_regime="Low Volume",
        entry_logic="Volume spike with directional price confirmation",
        exit_logic="Volume exhaustion or fixed target",
        tags=("volume", "spike", "confirmation"),
    ),
    "strategies.volume.volume_signal",
))

# ---- Donchian family ----
strategy_registry.register(CandleSignalPlugin(
    StrategyMetadata(
        id="donchian", name="Donchian Breakout", category="Trend Following",
        family="Trend Following", variant="Donchian Channel",
        description="Donchian channel breakout with period-based entries.",
        style="Swing", risk_profile="moderate", icon="📏",
        min_candles=30, status="backtested",
        best_regime="Trending", worst_regime="Ranging / Choppy",
        entry_logic="Price breaks above upper Donchian channel (long) or below lower (short)",
        exit_logic="Opposite channel break or fixed target",
        tags=("donchian", "channel", "breakout", "trend"),
    ),
    "strategies.donchian.donchian_signal",
))

# ---- KAMA family ----
strategy_registry.register(CandleSignalPlugin(
    StrategyMetadata(
        id="kama", name="KAMA Adaptive", category="Trend Following",
        family="Trend Following", variant="Kaufman Adaptive",
        description="Kaufman Adaptive Moving Average with efficiency ratio.",
        style="Swing", risk_profile="moderate", icon="🎯",
        min_candles=50, status="backtested",
        best_regime="Trending", worst_regime="Choppy / Noisy",
        entry_logic="Price crosses above KAMA with high efficiency ratio (long) or inverse",
        exit_logic="KAMA direction reversal",
        tags=("kama", "adaptive", "efficiency-ratio"),
    ),
    "strategies.kama.kama_signal",
))

# ---- Divergence family ----
strategy_registry.register(CandleSignalPlugin(
    StrategyMetadata(
        id="divergence", name="RSI/MACD Divergence", category="Momentum",
        family="Momentum", variant="Divergence",
        description="Bullish/bearish divergence between price and RSI/MACD.",
        style="Swing", risk_profile="moderate", icon="↗️",
        min_candles=50, status="backtested",
        best_regime="Trend Exhaustion", worst_regime="Strong Momentum",
        entry_logic="Price makes new low but RSI/MACD makes higher low (bullish divergence)",
        exit_logic="Momentum confirmation or fixed target",
        tags=("divergence", "rsi", "macd", "reversal"),
    ),
    "strategies.divergence.divergence_signal",
))

# ---- Cambridge Hook family ----
strategy_registry.register(CandleSignalPlugin(
    StrategyMetadata(
        id="cambridge_hook", name="Cambridge Hook", category="Price Action",
        family="Price Action", variant="Cambridge Hook",
        description="Cambridge hook pattern with ADX and moving average confirmation.",
        style="Swing", risk_profile="moderate", icon="🪝",
        min_candles=50, status="backtested",
        best_regime="Trending", worst_regime="Choppy / No Trend",
        entry_logic="Hook pattern formation with ADX > 20 and MA confirmation",
        exit_logic="Pattern invalidation or fixed target",
        tags=("cambridge-hook", "pattern", "adx"),
    ),
    "strategies.cambridge_hook.cambridge_hook_signal",
))

# ---- Wyckoff family ----
strategy_registry.register(CandleSignalPlugin(
    StrategyMetadata(
        id="wyckoff", name="Wyckoff Accumulation", category="Volume",
        family="Volume", variant="Wyckoff",
        description="Wyckoff accumulation/distribution phase detection.",
        style="Swing", risk_profile="moderate", icon="🏗️",
        min_candles=50, status="experimental",
        best_regime="Range-bound", worst_regime="Strong Trending",
        entry_logic="Spring/upthrust detection with volume confirmation in accumulation/distribution",
        exit_logic="Phase transition or fixed target",
        tags=("wyckoff", "accumulation", "distribution", "volume"),
    ),
    "strategies.wyckoff.wyckoff_signal",
))

# ---- Crypto family ----
strategy_registry.register(CandleSignalPlugin(
    StrategyMetadata(
        id="crypto", name="Crypto Sentiment", category="Crypto",
        family="Crypto", variant="Funding & OI",
        description="Crypto-specific signals using funding rate and open interest proxies.",
        style="Intraday", risk_profile="aggressive", icon="₿",
        min_candles=50, status="experimental",
        best_regime="High Volume Crypto", worst_regime="Low Liquidity",
        entry_logic="Funding rate and open interest divergence from price",
        exit_logic="Sentiment normalization",
        tags=("crypto", "funding", "open-interest", "sentiment"),
    ),
    "strategies.crypto.crypto_signal",
))

# ---- Custom DSL ----
strategy_registry.register(CustomDslStrategyPlugin())
