"""Bridges the indicator registry and ICT analysis into chart payload data.

Kept separate from ``charting`` so the chart component stays a renderer and all
indicator/structure selection logic remains independently testable.
"""

from __future__ import annotations

import math

import pandas as pd

from platform_core.indicators import INDICATOR_REGISTRY


OVERLAY_COLORS = {
    "SMA": "#f2b705", "EMA": "#1e6bff", "WMA": "#8d99ae", "HMA": "#7048e8",
    "VWAP": "#2f6fed", "SUPERTREND": "#00a896", "BB": "#6c757d", "KC": "#adb5bd",
    "VOLUME_MA": "#495057", "OBV": "#20c997", "SUPPORT_RESISTANCE": "#495057",
    "SWING_HIGH": "#ef5350", "SWING_LOW": "#26a69a",
    "PREVIOUS_HIGH_LOW": "#868e96", "SESSION_HIGH_LOW": "#868e96",
    "RSI": "#7048e8", "MACD": "#1e6bff", "STOCHASTIC": "#f76707",
    "STOCHASTIC_RSI": "#f76707", "ATR": "#212529",
}

DEFAULT_OVERLAYS = ("EMA", "SUPERTREND")
DEFAULT_SUB_PANES = ("RSI",)


def _points_from_series(series: pd.Series) -> list:
    return [
        {"time": int(stamp.value // 1_000_000_000), "value": float(value)}
        for stamp, value in series.items()
        if value is not None and math.isfinite(float(value))
    ]


def build_overlay_series(frame: pd.DataFrame, selections: list | None = None) -> list:
    """Compute overlay series for the requested indicators.

    ``selections`` is a list of ``{"id": ..., "params": {...}}`` entries.
    An indicator that cannot be computed is skipped rather than raising, so a
    single bad parameter never breaks the chart.
    """
    if frame is None or frame.empty:
        return []
    if selections is None:
        selections = [{"id": name} for name in (*DEFAULT_OVERLAYS, *DEFAULT_SUB_PANES)]
    overlays: list = []
    for selection in selections:
        if isinstance(selection, str):
            identifier, params = selection, {}
        elif isinstance(selection, dict):
            identifier = str(selection.get("id"))
            params = dict(selection.get("params") or {})
        else:
            continue
        spec = INDICATOR_REGISTRY.get(identifier)
        if spec is None:
            continue
        try:
            values = spec.compute(frame, **params)
        except (ValueError, KeyError, TypeError):
            continue
        for output in spec.outputs:
            points = _points_from_series(values[output])
            if not points:
                continue
            label = identifier if output.lower() == identifier.lower() else f"{identifier} {output}"
            overlays.append({
                "name": label,
                "pane": spec.pane,
                "color": OVERLAY_COLORS.get(identifier, "#1e6bff"),
                "width": 2 if spec.overlay else 1,
                "line_style": 0,
                "data": points,
            })
    return overlays


def build_ict_overlay(analysis: dict | None, toggles: dict | None = None) -> dict | None:
    """Translate an ICT analysis into chart structures, honouring display toggles.

    Nothing is invented: with no detected structure the chart shows nothing.
    """
    if not analysis:
        return None
    toggles = toggles or {}
    result = {"lines": [], "boxes": [], "markers": []}
    overlays = analysis.get("overlays") or {}
    liquidity = overlays.get("liquidity") or {}
    dealing = overlays.get("dealing_range") or {}

    if toggles.get("liquidity", True) and liquidity:
        result["lines"].append({"price": liquidity.get("sell_side"), "color": "#ef5350",
                                "title": "BSL", "line_style": 0})
        result["lines"].append({"price": liquidity.get("buy_side"), "color": "#26a69a",
                                "title": "SSL", "line_style": 0})
    if toggles.get("premium_discount", True) and dealing:
        result["lines"].append({"price": dealing.get("equilibrium"), "color": "#8d99ae",
                                "title": "EQ", "line_style": 2})
    if toggles.get("fvg", True) and overlays.get("fvg"):
        for name, color in (("bullish", "#2f6fed"), ("bearish", "#f28b82")):
            gap = overlays["fvg"].get(name)
            if not isinstance(gap, dict):
                continue
            if gap.get("low") is not None and gap.get("high") is not None:
                result["boxes"].append({
                    "top": float(gap["high"]), "bottom": float(gap["low"]),
                    "color": color, "label": name.title() + " FVG",
                })
    structure = overlays.get("structure") or {}
    if toggles.get("mss", True):
        if structure.get("bullish_mss"):
            result["markers"].append({"text": "MSS", "position": "belowBar",
                                      "color": "#26a69a", "shape": "arrowUp"})
        if structure.get("bearish_mss"):
            result["markers"].append({"text": "MSS", "position": "aboveBar",
                                      "color": "#ef5350", "shape": "arrowDown"})
    if toggles.get("displacement", True):
        displacement = overlays.get("displacement") or {}
        if displacement.get("bullish"):
            result["markers"].append({"text": "D", "position": "belowBar",
                                      "color": "#1e6bff", "shape": "circle"})
        if displacement.get("bearish"):
            result["markers"].append({"text": "D", "position": "aboveBar",
                                      "color": "#1e6bff", "shape": "circle"})
    if toggles.get("levels", True) and analysis.get("has_setup"):
        for key, color, label in (("entry", "#1e6bff", "Entry"),
                                  ("stop", "#ef5350", "SL"),
                                  ("target", "#26a69a", "TP")):
            if analysis.get(key) is not None:
                result["lines"].append({"price": analysis[key], "color": color,
                                        "title": label, "line_style": 1})
    # Drop entries without a usable price so the component never receives nulls.
    result["lines"] = [line for line in result["lines"]
                       if line.get("price") is not None
                       and math.isfinite(float(line["price"]))]
    if not (result["lines"] or result["boxes"] or result["markers"]):
        return None
    return result