"""ICT strategy adapter with structured, explainable diagnostics.

The existing detection primitives in ``strategies.ict`` / ``strategy`` remain the
single source of truth for every ICT concept. This module calls them, keeps the
identical scoring rules, and additionally reports which stage passed or failed
so the UI can explain the setup instead of showing a generic error.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from platform_core.errors import PlatformError
from platform_core.indicators import atr as atr_indicator
from platform_core.status import Status


LOGGER = logging.getLogger("platform_core.ict")

ICT_MIN_CANDLES = 100


@dataclass(frozen=True)
class IctStage:
    """One link in the ICT decision chain, e.g. SWEEP or MSS."""

    name: str
    passed: bool
    detail: str = ""
    score: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "passed": self.passed, "detail": self.detail,
                "score": self.score}


@dataclass(frozen=True)
class IctAnalysis:
    """Full, inspectable result of one ICT evaluation."""

    side: str
    score: int
    reason: str
    htf_bias: str
    htf_reason: str
    long_score: int
    short_score: int
    stages: tuple = ()
    entry: float | None = None
    stop: float | None = None
    target: float | None = None
    risk_distance: float | None = None
    overlays: dict = field(default_factory=dict)
    price: float = 0.0
    timestamp: pd.Timestamp | None = None

    @property
    def has_setup(self) -> bool:
        return self.side in {"LONG", "SHORT"}

    @property
    def failed_stages(self) -> list:
        return [stage for stage in self.stages if not stage.passed]

    def to_dict(self) -> dict[str, Any]:
        return {
            "side": self.side,
            "score": self.score,
            "reason": self.reason,
            "htf_bias": self.htf_bias,
            "htf_reason": self.htf_reason,
            "long_score": self.long_score,
            "short_score": self.short_score,
            "has_setup": self.has_setup,
            "price": self.price,
            "timestamp": self.timestamp.isoformat() if self.timestamp is not None else None,
            "entry": self.entry,
            "stop": self.stop,
            "target": self.target,
            "risk_distance": self.risk_distance,
            "rr": (self.risk_distance and (abs(self.target - self.entry) / self.risk_distance))
            if self.entry and self.stop and self.target and self.risk_distance else None,
            "stages": [stage.to_dict() for stage in self.stages],
            "failed_stages": [stage.name for stage in self.failed_stages],
            "overlays": self.overlays,
        }


def candles_from_frame(frame: pd.DataFrame) -> list:
    """Convert a normalized OHLCV frame to the positional rows strategies use."""
    return [
        [int(timestamp.value // 1_000_000), float(row.open), float(row.high),
         float(row.low), float(row.close), float(row.volume)]
        for timestamp, row in frame.iterrows()
    ]


def _atr_levels(frame: pd.DataFrame, side: str, rr: float, period: int = 14,
                multiplier: float = 1.5) -> dict:
    """Derive stop/target from real ATR. Returns {} when ATR is unavailable."""
    values = atr_indicator(frame, period).dropna()
    if values.empty or float(values.iloc[-1]) <= 0:
        return {}
    entry = float(frame["close"].iloc[-1])
    risk_distance = float(values.iloc[-1]) * multiplier
    stop = entry - risk_distance if side == "LONG" else entry + risk_distance
    target = entry + rr * risk_distance if side == "LONG" else entry - rr * risk_distance
    return {"entry": entry, "stop": stop, "target": target, "risk_distance": risk_distance}


def analyze_ict(frame: pd.DataFrame, htf_frame: pd.DataFrame | None = None, rr: float = 2.0,
                atr_period: int = 14, atr_multiplier: float = 1.5) -> IctAnalysis:
    """Run the existing ICT logic and return a fully explained result.

    ``htf_frame`` is optional; without it the existing behaviour of using the
    same series for both timeframes is preserved.
    """
    if frame is None or frame.empty:
        raise PlatformError("No market data is available for the ICT analysis.",
                            Status.DATA_UNAVAILABLE)
    if len(frame) < ICT_MIN_CANDLES:
        raise PlatformError(
            f"Insufficient market data: the ICT strategy needs at least "
            f"{ICT_MIN_CANDLES} finalized candles; {len(frame)} were provided.",
            Status.INSUFFICIENT_DATA,
        )

    from strategies.ict import ict_signal

    candles = candles_from_frame(frame)
    htf_candles = candles_from_frame(htf_frame) if htf_frame is not None else None
    try:
        result = ict_signal(candles, htf_candles)
        detail = analyze_ict_stages(candles, htf_candles, frame)
    except PlatformError:
        raise
    except Exception as exc:
        LOGGER.exception("ICT strategy failed on %s candles", len(candles))
        raise PlatformError(
            "The ICT strategy could not complete its calculation on this dataset.",
            Status.STRATEGY_ERROR,
            {"error_type": type(exc).__name__},
        ) from exc

    if not isinstance(result, dict) or "side" not in result:
        raise PlatformError("The ICT strategy returned an unexpected result shape.",
                            Status.STRATEGY_ERROR)

    side = str(result.get("side", "NEUTRAL"))
    score = int(result.get("score", 0) or 0)
    levels = _atr_levels(frame, side, rr, atr_period, atr_multiplier) if side in {"LONG", "SHORT"} else {}
    return IctAnalysis(
        side=side,
        score=score,
        reason=str(result.get("reason", "")),
        htf_bias=detail["htf_bias"],
        htf_reason=detail["htf_reason"],
        long_score=detail["long_score"],
        short_score=detail["short_score"],
        stages=detail["stages"],
        entry=levels.get("entry"),
        stop=levels.get("stop"),
        target=levels.get("target"),
        risk_distance=levels.get("risk_distance"),
        overlays=detail["overlays"],
        price=float(frame["close"].iloc[-1]),
        timestamp=frame.index[-1],
    )


def analyze_ict_stages(candles: list, htf_candles: list | None,
                       frame: pd.DataFrame) -> dict:
    """Recompute each ICT condition so the UI can show exactly what was detected.

    This uses the same primitive functions as ``strategies.ict.ict_signal``; it
    never invents a condition that the strategy did not evaluate.
    """
    from strategy import (
        detect_bearish_mss,
        detect_bullish_mss,
        detect_displacement,
        detect_liquidity_sweep,
        find_bearish_fvg,
        find_bearish_order_block,
        find_bullish_fvg,
        find_bullish_order_block,
    )
    from strategies.ict import _detect_htf_bias

    if htf_candles is None:
        htf_candles = candles
    htf_bias, htf_reason = _detect_htf_bias(htf_candles)

    price = float(candles[-1][4])
    recent_high = max(float(candle[2]) for candle in candles[-50:-1])
    recent_low = min(float(candle[3]) for candle in candles[-50:-1])

    bullish_sweep = detect_liquidity_sweep(candles, recent_low, "bullish")
    bearish_sweep = detect_liquidity_sweep(candles, recent_high, "bearish")
    bullish_displacement = detect_displacement(candles, "bullish")
    bearish_displacement = detect_displacement(candles, "bearish")
    bullish_mss = detect_bullish_mss(candles)
    bearish_mss = detect_bearish_mss(candles)
    bullish_fvg = find_bullish_fvg(candles)
    bearish_fvg = find_bearish_fvg(candles)
    bullish_ob = find_bullish_order_block(candles)
    bearish_ob = find_bearish_order_block(candles)

    long_score = 0
    short_score = 0
    if bullish_sweep:
        long_score += 25
    if bullish_displacement:
        long_score += 20
    if bullish_mss:
        long_score += 20
    if bullish_fvg is not None:
        long_score += 15
    if bullish_ob is not None:
        long_score += 10
    if bullish_fvg is not None and price <= float(bullish_fvg["mid"]):
        long_score += 10
    if bearish_sweep:
        short_score += 25
    if bearish_displacement:
        short_score += 20
    if bearish_mss:
        short_score += 20
    if bearish_fvg is not None:
        short_score += 15
    if bearish_ob is not None:
        short_score += 10
    if bearish_fvg is not None and price >= float(bearish_fvg["mid"]):
        short_score += 10

    if htf_bias == "BULLISH":
        short_score = 0
    elif htf_bias == "BEARISH":
        long_score = 0

    stage_specs = (
        ("HTF_BIAS", htf_bias != "NEUTRAL", htf_reason),
        ("LIQUIDITY_SWEEP", bool(bullish_sweep or bearish_sweep),
         "Sell-side sweep detected" if bullish_sweep else
         "Buy-side sweep detected" if bearish_sweep else "No sweep on the latest candle"),
        ("DISPLACEMENT", bool(bullish_displacement or bearish_displacement),
         "Displacement candle confirmed" if (bullish_displacement or bearish_displacement)
         else "No displacement candle"),
        ("MSS", bool(bullish_mss or bearish_mss),
         "Bullish MSS" if bullish_mss else "Bearish MSS" if bearish_mss
         else "No market structure shift"),
        ("FVG", bool(bullish_fvg or bearish_fvg),
         "Fair value gap present" if (bullish_fvg or bearish_fvg) else "No fair value gap"),
        ("ORDER_BLOCK", bool(bullish_ob or bearish_ob),
         "Order block present" if (bullish_ob or bearish_ob) else "No order block"),
        ("SCORE_THRESHOLD", max(long_score, short_score) >= 60,
         f"Long score {long_score} / short score {short_score}; 60 is required"),
    )
    stages = tuple(IctStage(name, bool(passed), str(detail)) for name, passed, detail in stage_specs)

    overlays = {
        "liquidity": {
            "sell_side": float(recent_low),
            "buy_side": float(recent_high),
            "sweep_detected": bool(bullish_sweep or bearish_sweep),
        },
        "fvg": {
            "bullish": bullish_fvg,
            "bearish": bearish_fvg,
        },
        "order_block": {
            "bullish": bullish_ob,
            "bearish": bearish_ob,
        },
        "structure": {
            "bullish_mss": bool(bullish_mss),
            "bearish_mss": bool(bearish_mss),
        },
        "displacement": {
            "bullish": bool(bullish_displacement),
            "bearish": bool(bearish_displacement),
        },
        "dealing_range": {
            "high": float(recent_high),
            "low": float(recent_low),
            "equilibrium": (recent_high + recent_low) / 2,
        },
        "htf_bias": htf_bias,
    }
    return {
        "htf_bias": htf_bias,
        "htf_reason": htf_reason,
        "long_score": long_score,
        "short_score": short_score,
        "stages": stages,
        "overlays": overlays,
    }