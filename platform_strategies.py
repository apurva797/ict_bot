"""Shared, deterministic strategy plugin contract used by the Streamlit platform.

Plugins only produce signals. Existing backtest and paper-trading engines remain
responsible for fills, risk limits, fees, cooldowns, and position state.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import pandas as pd

from demo_strategy import evaluate_conditions, indicators, validate_strategy


@dataclass(frozen=True)
class StrategyMetadata:
    id: str
    name: str
    description: str
    category: str
    version: str = "1.0.0"
    supported_markets: tuple[str, ...] = ("crypto spot",)
    supported_timeframes: tuple[str, ...] = ("5m", "15m", "1h")
    min_candles: int = 35


@dataclass(frozen=True)
class StrategySignal:
    side: str
    timestamp: Any
    explanation: str
    features: dict[str, float]


class StrategyPlugin(Protocol):
    metadata: StrategyMetadata

    def signal_series(self, frame: pd.DataFrame, parameters: dict[str, Any] | None = None) -> pd.Series:
        """Return one of BUY, SELL, or None per candle; never access future rows."""


class StrategyRegistry:
    """Small in-process registry; registering a duplicate id is an error."""

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


class IctStrategyPlugin:
    metadata = StrategyMetadata(
        id="ict", name="ICT", category="Built-in", min_candles=100,
        description="Existing liquidity sweep, displacement, MSS, FVG, order block, and HTF-bias signal logic.",
    )

    def signal_series(self, frame, parameters=None):
        from strategies.ict import ict_signal

        signals = pd.Series(None, index=frame.index, dtype="object")
        for end in range(self.metadata.min_candles, len(frame) + 1):
            candles = [
                [int(ts.timestamp() * 1000), float(row.open), float(row.high), float(row.low), float(row.close), float(row.volume)]
                for ts, row in frame.iloc[:end].iterrows()
            ]
            result = ict_signal(candles)
            side = result.get("side") if isinstance(result, dict) else None
            if side in {"LONG", "SHORT"}:
                signals.iloc[end - 1] = "BUY" if side == "LONG" else "SELL"
        return signals


class QuantStrategyPlugin:
    """A parameterized indicator-rule adapter; variants share the same engine."""

    def __init__(self, strategy_id, name, description, entry, exit_, min_candles=35):
        self.metadata = StrategyMetadata(
            id=strategy_id, name=name, description=description, category="Quant",
            min_candles=min_candles,
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


class CustomDslStrategyPlugin:
    metadata = StrategyMetadata(
        id="custom.dsl", name="Custom strategy", category="Custom",
        description="Validated, deterministic strategy rules from the restricted strategy DSL.",
    )

    def signal_series(self, frame, parameters=None):
        spec = validate_strategy(parameters or {})
        signals = pd.Series(None, index=frame.index, dtype="object")
        entry_mask = evaluate_conditions(frame, spec["entry"]).fillna(False)
        signals.loc[entry_mask] = spec["side"]
        return signals


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


strategy_registry = StrategyRegistry()
strategy_registry.register(IctStrategyPlugin())
strategy_registry.register(QuantStrategyPlugin("quant.trend", "EMA trend following", "Configurable fast/slow EMA crossover.", _ema_cross_entry, _ema_cross_exit))
strategy_registry.register(QuantStrategyPlugin("quant.mean_reversion", "RSI mean reversion", "Configurable oversold/overbought RSI rules.", _rsi_entry, _rsi_exit, min_candles=35))
strategy_registry.register(CustomDslStrategyPlugin())

