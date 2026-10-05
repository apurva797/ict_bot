"""Stable TradingView Lightweight Charts component for normalized app OHLCV.

In addition to candles and volume the component can render:
  * indicator line series computed in Python (``overlays``)
  * ICT structures detected by the strategy (``ict``) as price lines, shaded
    zones, and level markers
  * user drawings (horizontal/vertical lines, trend lines, rectangles, text)

Every overlay value is computed from real candles. Nothing here is decorative.
"""

from __future__ import annotations

import hashlib
import json
import math

import pandas as pd
import streamlit as st

LIGHTWEIGHT_CHARTS_VERSION = "5.2.0"
CHART_HEIGHT = 470
_REQUIRED_COLUMNS = ("open", "high", "low", "close", "volume")

# Fallback chart theme matching the component's original light styling; used
# when a caller (or a unit test) does not supply an active palette.
_DEFAULT_CHART_THEME = {
    "background": "#ffffff",
    "text": "#222222",
    "grid": "#f1f3f5",
    "crosshair": "#9aa4b2",
    "accent": "#147d92",
    "up": "#26a69a",
    "down": "#ef5350",
    "up_volume": "rgba(38, 166, 154, 0.45)",
    "down_volume": "rgba(239, 83, 80, 0.45)",
}


def _rgba(hex_color: str, alpha: float) -> str:
    """``#rrggbb`` + alpha → ``rgba(...)`` for volume-bar fills."""
    value = str(hex_color).lstrip("#")
    if len(value) != 6:
        return f"rgba(128, 128, 128, {alpha})"
    try:
        channels = [int(value[index:index + 2], 16) for index in (0, 2, 4)]
    except ValueError:
        return f"rgba(128, 128, 128, {alpha})"
    return f"rgba({channels[0]}, {channels[1]}, {channels[2]}, {alpha})"


def chart_theme(palette: dict | None) -> dict:
    """Map design-system tokens onto Lightweight Charts colour slots.

    The chart sits directly on the page background, so it must paint with the
    same palette as everything around it; a white chart inside a dark
    terminal would break the one-surface rule the product follows.
    """
    if not palette:
        return dict(_DEFAULT_CHART_THEME)
    return {
        "background": palette.get("bg", _DEFAULT_CHART_THEME["background"]),
        "text": palette.get("text_faint", _DEFAULT_CHART_THEME["text"]),
        "grid": palette.get("border_subtle", _DEFAULT_CHART_THEME["grid"]),
        "crosshair": palette.get("border_strong", _DEFAULT_CHART_THEME["crosshair"]),
        "accent": palette.get("accent", _DEFAULT_CHART_THEME["accent"]),
        "up": palette.get("profit", _DEFAULT_CHART_THEME["up"]),
        "down": palette.get("loss", _DEFAULT_CHART_THEME["down"]),
        "up_volume": _rgba(palette.get("profit", "#26a69a"), 0.45),
        "down_volume": _rgba(palette.get("loss", "#ef5350"), 0.45),
    }


def normalize_chart_ohlcv(frame: pd.DataFrame) -> pd.DataFrame:
    """Return valid UTC OHLCV indexed by unique, ascending Unix-second candles.

    Chart timestamps are deliberately reduced to whole seconds because that is
    the Lightweight Charts intraday time unit used by this app. If source rows
    collapse to the same second, the last valid row wins.
    """
    if frame is None or frame.empty:
        return pd.DataFrame(columns=_REQUIRED_COLUMNS,
                            index=pd.DatetimeIndex([], tz="UTC", name="timestamp"))

    missing = set(_REQUIRED_COLUMNS).difference(frame.columns)
    if missing:
        raise ValueError(f"Chart OHLCV is missing columns: {', '.join(sorted(missing))}")

    result = frame.loc[:, _REQUIRED_COLUMNS].copy()
    timestamps = pd.to_datetime(frame.index, utc=True, errors="coerce")
    result.index = pd.DatetimeIndex(timestamps, name="timestamp")
    for column in _REQUIRED_COLUMNS:
        result[column] = pd.to_numeric(result[column], errors="coerce")

    result = result.loc[~result.index.isna()]
    result = result.replace([float("inf"), float("-inf")], float("nan")).dropna()
    result = result.loc[
        (result["open"] > 0)
        & (result["high"] > 0)
        & (result["low"] > 0)
        & (result["close"] > 0)
        & (result["volume"] >= 0)
        & (result["high"] >= result[["open", "low", "close"]].max(axis=1))
        & (result["low"] <= result[["open", "high", "close"]].min(axis=1))
    ]

    # Flooring to seconds gives a deterministic, duplicate-free chart time key.
    result.index = pd.DatetimeIndex(result.index.floor("s"), tz="UTC", name="timestamp")
    result = result.sort_index(kind="stable")
    result = result.loc[~result.index.duplicated(keep="last")]
    return result


def _chart_rows(frame: pd.DataFrame,
                theme: dict | None = None) -> tuple[list[dict], list[dict]]:
    colors = theme or _DEFAULT_CHART_THEME
    candles: list[dict] = []
    volumes: list[dict] = []
    for timestamp, row in frame.iterrows():
        time_seconds = int(timestamp.value // 1_000_000_000)
        candles.append({
            "time": time_seconds,
            "open": float(row.open),
            "high": float(row.high),
            "low": float(row.low),
            "close": float(row.close),
        })
        volumes.append({
            "time": time_seconds,
            "value": float(row.volume),
            "color": colors["up_volume"] if row.close >= row.open
            else colors["down_volume"],
        })
    return candles, volumes


def chart_update_kind(previous: list[dict], current: list[dict],
                     previous_volumes: list[dict] | None = None,
                     current_volumes: list[dict] | None = None) -> str:
    """Classify a data change for testable initial/incremental chart updates."""
    previous_volumes = previous_volumes or []
    current_volumes = current_volumes or []
    if previous == current and previous_volumes == current_volumes:
        return "unchanged"
    if not previous:
        return "initial"
    if (len(current) > len(previous) and current[:len(previous)] == previous
            and previous_volumes == current_volumes[:len(previous_volumes)]):
        return "append"
    if (len(current) == len(previous) and current[:-1] == previous[:-1]
            and current and current[-1].get("time") == previous[-1].get("time")
            and previous_volumes[:-1] == current_volumes[:-1]
            and (not previous_volumes or previous_volumes[-1].get("time") == current_volumes[-1].get("time"))):
        return "latest"
    return "reload"


def make_chart_payload(frame: pd.DataFrame, title: str, dataset_id: str,
                       reset_id: int = 0, height: int = CHART_HEIGHT,
                       overlays: list | None = None, ict: dict | None = None,
                       drawings: list | None = None,
                       price_decimals: int | None = None,
                       palette: dict | None = None) -> dict:
    """Build the JSON payload for one chart render.

    ``overlays`` are pre-computed indicator series, ``ict`` holds detected
    structures, and ``drawings`` are user annotations. All are optional so the
    existing call sites keep working unchanged. ``palette`` is the active
    design-system token set, mapped to chart colours via :func:`chart_theme`;
    when omitted the original light styling is used.
    """
    normalized = normalize_chart_ohlcv(frame)
    colors = chart_theme(palette)
    candles, volumes = _chart_rows(normalized, colors)
    payload_overlays = _sanitize_overlays(overlays or [], normalized)
    payload_ict = _sanitize_ict(ict, normalized)
    payload_drawings = _sanitize_drawings(drawings or [])
    encoded = json.dumps(
        [candles, volumes, payload_overlays, payload_ict, payload_drawings],
        separators=(",", ":"), allow_nan=False, sort_keys=True,
    )
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    return {
        "candles": candles,
        "volumes": volumes,
        "overlays": payload_overlays,
        "ict": payload_ict,
        "drawings": payload_drawings,
        "price_decimals": price_decimals,
        "digest": digest,
        "dataset_id": str(dataset_id),
        "title": str(title),
        "reset_id": int(reset_id),
        "height": max(300, int(height)),
        "theme": colors,
    }


def _time_seconds(index) -> int:
    return int(pd.Timestamp(index).value // 1_000_000_000)


def _sanitize_overlays(overlays: list, frame: pd.DataFrame) -> list[dict]:
    """Keep only finite overlay points that align with real candle times."""
    valid_times = {_time_seconds(stamp) for stamp in frame.index}
    cleaned: list[dict] = []
    for overlay in overlays:
        if not isinstance(overlay, dict):
            continue
        name = str(overlay.get("name") or overlay.get("id") or "indicator")
        pane = str(overlay.get("pane") or "price")
        series = overlay.get("series") or overlay.get("data")
        if not series:
            continue
        rows = []
        for point in series:
            if not isinstance(point, dict):
                continue
            stamp = point.get("time")
            value = point.get("value")
            if stamp is None or value is None:
                continue
            value = float(value)
            if not math.isfinite(value):
                continue
            time_value = int(stamp)
            if valid_times and time_value not in valid_times:
                continue
            rows.append({"time": time_value, "value": value})
        if rows:
            cleaned.append({
                "name": name,
                "pane": pane,
                "color": str(overlay.get("color") or "#1e6bff"),
                "width": int(overlay.get("width") or 2),
                "line_style": int(overlay.get("line_style") or 0),
                "data": rows,
            })
    return cleaned


def _sanitize_ict(ict: dict | None, frame: pd.DataFrame) -> dict:
    """Map detected ICT structures onto chart primitives, skipping absent ones."""
    empty = {"lines": [], "boxes": [], "markers": []}
    if not ict or frame.empty:
        return empty
    last_time = _time_seconds(frame.index[-1])
    lines: list[dict] = []
    boxes: list[dict] = []
    markers: list[dict] = []

    def add_line(key: str, value, color: str, title: str, style: int = 0) -> None:
        if value is None:
            return
        try:
            price = float(value)
        except (TypeError, ValueError):
            return
        if not math.isfinite(price):
            return
        lines.append({"price": price, "color": color, "title": title,
                      "line_style": style, "axis_label_visible": True})

    liquidity = ict.get("liquidity") or {}
    dealing = ict.get("dealing_range") or {}
    if liquidity:
        add_line("sell_side", liquidity.get("sell_side"), "#ef5350", "BSL / sell-side")
        add_line("buy_side", liquidity.get("buy_side"), "#26a69a", "SSL / buy-side")
    if dealing:
        add_line("equilibrium", dealing.get("equilibrium"), "#8d99ae", "Equilibrium", 2)

    for name in ("bullish", "bearish"):
        gap = (ict.get("fvg") or {}).get(name)
        if not isinstance(gap, dict):
            continue
        low, high = gap.get("low"), gap.get("high")
        if low is None or high is None:
            continue
        try:
            low, high = float(low), float(high)
        except (TypeError, ValueError):
            continue
        if not (math.isfinite(low) and math.isfinite(high)):
            continue
        start_time = _time_seconds(frame.index[-1]) - max(3, len(frame) // 8) * 300
        boxes.append({
            "top": high, "bottom": low,
            "start_time": max(int(frame.index[0].value // 1_000_000_000), start_time),
            "end_time": last_time,
            "color": "#2f6fed" if name == "bullish" else "#f28b82",
            "label": f"{'Bullish' if name == 'bullish' else 'Bearish'} FVG",
        })

    structure = ict.get("structure") or {}
    if structure.get("bullish_mss"):
        markers.append({"time": last_time, "position": "belowBar", "color": "#26a69a",
                        "shape": "arrowUp", "text": "MSS"})
    if structure.get("bearish_mss"):
        markers.append({"time": last_time, "position": "aboveBar", "color": "#ef5350",
                        "shape": "arrowDown", "text": "MSS"})
    displacement = ict.get("displacement") or {}
    if displacement.get("bullish"):
        markers.append({"time": last_time, "position": "belowBar", "color": "#1e6bff",
                        "shape": "circle", "text": "Disp"})
    if displacement.get("bearish"):
        markers.append({"time": last_time, "position": "aboveBar", "color": "#1e6bff",
                        "shape": "circle", "text": "Disp"})

    for name, key in (("bullish", "entry"), ("bearish", "entry")):
        level = ict.get(key)
        if level is None:
            continue
        color = "#26a69a" if name == "bullish" else "#ef5350"
        add_line(key, level, color, "Entry", 1)
    add_line("stop_loss", ict.get("stop_loss"), "#ef5350", "Stop loss", 2)
    add_line("take_profit", ict.get("take_profit"), "#26a69a", "Take profit", 2)
    return {"lines": lines, "boxes": boxes, "markers": markers}


def _sanitize_drawings(drawings: list) -> list[dict]:
    """Validate user drawings so a malformed annotation cannot break the chart."""
    cleaned: list[dict] = []
    for drawing in drawings:
        if not isinstance(drawing, dict):
            continue
        kind = str(drawing.get("kind") or "").strip()
        if kind not in {"horizontal_line", "vertical_line", "trend_line", "rectangle", "text"}:
            continue
        points = drawing.get("points") or []
        try:
            points = [[float(point[0]), float(point[1])] for point in points]
        except (TypeError, ValueError, IndexError):
            continue
        if not all(len(point) == 2 and all(math.isfinite(value) for value in point)
                   for point in points):
            continue
        needed = 1 if kind in {"horizontal_line", "vertical_line", "text"} else 2
        if kind == "text" and not str(drawing.get("text") or "").strip():
            continue
        if len(points) < needed:
            continue
        cleaned.append({
            "kind": kind,
            "points": points[:2],
            "color": str(drawing.get("color") or "#f2b705"),
            "text": str(drawing.get("text") or ""),
        })
    return cleaned


_HTML = '<div class="chart-status" aria-live="polite">Loading chart…</div><div class="chart-root" role="img" aria-label="OHLCV candlestick and volume chart"></div>'
_CSS = f"""
:host {{ display:block; width:100%; min-width:0; }}
.chart-status {{ box-sizing:border-box; height:30px; padding:6px 8px; color:var(--ui-text-faint, #6c7896); font:13px var(--ui-font, sans-serif); overflow:hidden; white-space:nowrap; text-overflow:ellipsis; }}
.chart-root {{ box-sizing:border-box; width:100%; height:{CHART_HEIGHT}px; min-height:300px; }}
"""
_JS = r"""
const LIBRARY_URL = "https://cdn.jsdelivr.net/npm/lightweight-charts@5.2.0/dist/lightweight-charts.standalone.production.js";

function loadLibrary() {
  if (window.LightweightCharts) return Promise.resolve(window.LightweightCharts);
  if (!window.__ictLightweightChartsPromise) {
    window.__ictLightweightChartsPromise = new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = LIBRARY_URL;
      script.async = true;
      script.onload = () => resolve(window.LightweightCharts);
      script.onerror = () => reject(new Error("Chart library could not be loaded"));
      document.head.appendChild(script);
    });
  }
  return window.__ictLightweightChartsPromise;
}

function saveViewport(state) {
  const timeScale = state.chart.timeScale();
  const priceScale = state.price.priceScale();
  try { state.timeRange = timeScale.getVisibleRange(); } catch (_) {}
  try { state.logicalRange = timeScale.getVisibleLogicalRange(); } catch (_) {}
  try { state.priceRange = priceScale.getVisibleRange(); } catch (_) {}
}

function restoreViewport(state) {
  const timeScale = state.chart.timeScale();
  const priceScale = state.price.priceScale();
  if (state.timeRange) {
    try { timeScale.setVisibleRange(state.timeRange); } catch (_) {}
  } else if (state.logicalRange) {
    try { timeScale.setVisibleLogicalRange(state.logicalRange); } catch (_) {}
  }
  if (state.priceRange) {
    try {
      priceScale.setAutoScale(false);
      priceScale.setVisibleRange(state.priceRange);
    } catch (_) {}
  }
}

// Paint layout/grid/crosshair/candles from the payload's palette so the chart
// always matches the page theme; a change re-applies options in place.
function applyTheme(state, theme) {
  if (!state.chart) return;
  const resolved = theme || {
    background: "#ffffff", text: "#222222", grid: "#f1f3f5",
    crosshair: "#9aa4b2", accent: "#147d92",
    up: "#26a69a", down: "#ef5350"
  };
  const key = JSON.stringify(resolved);
  if (state.themeKey === key) return;
  state.themeKey = key;
  state.chart.applyOptions({
    layout: { background: { type: "solid", color: resolved.background }, textColor: resolved.text, attributionLogo: true },
    grid: { vertLines: { color: resolved.grid }, horzLines: { color: resolved.grid } },
    crosshair: {
      vertLine: { color: resolved.crosshair, width: 1, style: 3, labelBackgroundColor: resolved.accent },
      horzLine: { color: resolved.crosshair, width: 1, style: 3, labelBackgroundColor: resolved.accent }
    },
    timeScale: { borderColor: resolved.grid },
    rightPriceScale: { borderColor: resolved.grid }
  });
  if (state.price) {
    state.price.applyOptions({
      upColor: resolved.up, downColor: resolved.down,
      wickUpColor: resolved.up, wickDownColor: resolved.down
    });
  }
}

function applyData(state, data) {
  if (!state.chart || !data || !Array.isArray(data.candles)) return;
  applyTheme(state, data.theme);
  const chartHeight = Math.max(300, Number(data.height) || 470);
  if (state.chartHeight !== chartHeight) {
    state.root.style.height = `${chartHeight}px`;
    state.chart.applyOptions({ height: chartHeight });
    state.chartHeight = chartHeight;
  }
  const pairChanged = state.datasetId !== data.dataset_id;
  const resetRequested = state.resetId !== data.reset_id;
  const digestChanged = state.digest !== data.digest;

  if (pairChanged && state.hasFit) saveViewport(state);

  if (digestChanged) {
    if (pairChanged || !state.candles.length) {
      state.price.setData(data.candles);
      state.volume.setData(data.volumes);
    } else {
      const updateKind = classify(state.candles, state.volumes, data.candles, data.volumes);
      if (updateKind === "append") {
        saveViewport(state);
        for (let i = state.candles.length; i < data.candles.length; i++) {
          state.price.update(data.candles[i]);
          state.volume.update(data.volumes[i]);
        }
      } else if (updateKind === "latest") {
        saveViewport(state);
        state.price.update(data.candles[data.candles.length - 1]);
        state.volume.update(data.volumes[data.volumes.length - 1]);
      } else if (updateKind === "reload") {
        saveViewport(state);
        state.price.setData(data.candles);
        state.volume.setData(data.volumes);
      }
    }
  }

  if (resetRequested || !state.hasFit) {
    state.chart.timeScale().fitContent();
    state.datasetId = data.dataset_id;
    state.resetId = data.reset_id;
    state.hasFit = true;
    state.digest = data.digest;
    state.candles = data.candles;
    state.volumes = data.volumes;
    // Fit once for a new dataset/reset, then lock the visible price range.
    const scale = state.price.priceScale();
    try {
      const visible = scale.getVisibleRange();
      scale.setAutoScale(false);
      if (visible) {
        state.priceRange = visible;
        scale.setVisibleRange(visible);
      }
    } catch (_) {}
  } else if (digestChanged || pairChanged) {
    restoreViewport(state);
  }

  state.datasetId = data.dataset_id;
  state.resetId = data.reset_id;
  state.digest = data.digest;
  state.candles = data.candles;
  state.volumes = data.volumes;

  const status = state.root.parentElement.querySelector(".chart-status");
  if (status) status.textContent = `${data.title} · finalized OHLCV · UTC`;
  renderOverlays(state, data);
}

// Draw indicator series, ICT structures, and user drawings from real values.
function renderOverlays(state, data) {
  if (!state.library) return;
  const overlays = Array.isArray(data.overlays) ? data.overlays : [];
  const overlayKey = JSON.stringify(overlays);
  if (state.overlayKey !== overlayKey) {
    for (const series of (state.overlaySeries || [])) {
      try { state.chart.removeSeries(series); } catch (_) {}
    }
    state.overlaySeries = [];
    for (const overlay of overlays) {
      try {
        const isVolume = overlay.pane === "volume";
        const series = state.chart.addSeries(state.library.LineSeries, {
          color: overlay.color, lineWidth: overlay.width, lineStyle: overlay.line_style,
          priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
          ...(isVolume ? { priceScaleId: "volume" } : {}),
        });
        series.setData(overlay.data.map((point) => ({ time: point.time, value: point.value })));
        state.overlaySeries.push(series);
      } catch (error) {
        if (state.status) state.status.textContent = "An indicator could not be drawn on the chart.";
      }
    }
    state.overlayKey = overlayKey;
  }

  const ict = data.ict || { lines: [], boxes: [], markers: [] };
  const ictKey = JSON.stringify(ict);
  if (state.ictKey === ictKey) return;
  state.ictKey = ictKey;

  for (const priceLine of (state.ictLines || [])) {
    try { state.price.removePriceLine(priceLine); } catch (_) {}
  }
  state.ictLines = [];
  for (const line of (ict.lines || [])) {
    try {
      state.ictLines.push(state.price.createPriceLine({
        price: line.price, color: line.color, lineWidth: 1,
        lineStyle: line.line_style || 0, axisLabelVisible: line.axis_label_visible !== false,
        title: line.title || "",
      }));
    } catch (_) {}
  }

  try {
    if (state.volume.setMarkers) state.volume.setMarkers(ict.markers || []);
  } catch (_) {}

  for (const box of (state.ictBoxes || [])) {
    try { state.price.removeSeries(box); } catch (_) {}
  }
  state.ictBoxes = [];
  for (const zone of (ict.boxes || [])) {
    try {
      state.ictBoxes.push(state.price.createPriceLine({
        price: zone.top, color: zone.color, lineWidth: 1, lineStyle: 0,
        axisLabelVisible: false, title: zone.label || "",
      }));
      state.ictBoxes.push(state.price.createPriceLine({
        price: zone.bottom, color: zone.color, lineWidth: 1, lineStyle: 0,
        axisLabelVisible: false, title: "",
      }));
    } catch (_) {}
  }
}

function classify(previous, previousVolumes, current, currentVolumes) {
  if (JSON.stringify(previous) === JSON.stringify(current) && JSON.stringify(previousVolumes) === JSON.stringify(currentVolumes)) return "unchanged";
  if (!previous.length) return "initial";
  if (current.length > previous.length && previous.every((bar, i) => JSON.stringify(bar) === JSON.stringify(current[i]) && JSON.stringify(previousVolumes[i]) === JSON.stringify(currentVolumes[i]))) return "append";
  if (current.length === previous.length && current.length && previous.slice(0, -1).every((bar, i) => JSON.stringify(bar) === JSON.stringify(current[i]) && JSON.stringify(previousVolumes[i]) === JSON.stringify(currentVolumes[i])) && current[current.length - 1].time === previous[previous.length - 1].time) return "latest";
  return "reload";
}

export default function(component) {
  const { data, parentElement } = component;
  let state = parentElement.__ictStableChart;
  if (!state) {
    const root = parentElement.querySelector(".chart-root");
    const status = parentElement.querySelector(".chart-status");
    state = parentElement.__ictStableChart = {
      root, status, chart: null, price: null, volume: null, observer: null,
      pendingData: data, digest: null, datasetId: null, resetId: null,
      candles: [], volumes: [], timeRange: null, logicalRange: null, priceRange: null, hasFit: false,
      loading: false, library: null
    };
  }
  state.pendingData = data;

  if (!state.chart && !state.loading) {
    state.loading = true;
    loadLibrary().then((library) => {
      state.library = library;
      const theme = (state.pendingData && state.pendingData.theme) || {
        background: "#ffffff", text: "#222222", grid: "#f1f3f5",
        crosshair: "#9aa4b2", accent: "#147d92",
        up: "#26a69a", down: "#ef5350"
      };
      state.chart = library.createChart(state.root, {
        width: Math.max(1, state.root.clientWidth), height: Math.max(300, Number(state.pendingData.height) || 470),
        layout: { background: { type: "solid", color: theme.background }, textColor: theme.text, attributionLogo: true },
        grid: { vertLines: { color: theme.grid }, horzLines: { color: theme.grid } },
        crosshair: {
          vertLine: { color: theme.crosshair, width: 1, style: 3, labelBackgroundColor: theme.accent },
          horzLine: { color: theme.crosshair, width: 1, style: 3, labelBackgroundColor: theme.accent }
        },
        timeScale: { timeVisible: true, secondsVisible: false, shiftVisibleRangeOnNewBar: false, borderColor: theme.grid },
        rightPriceScale: { borderColor: theme.grid },
        kineticScroll: { mouse: false, touch: false },
        handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
        handleScale: { axisPressedMouseMove: true, mouseWheel: true, pinch: true }
      });
      state.themeKey = JSON.stringify(theme);
      state.price = state.chart.addSeries(library.CandlestickSeries, {
        upColor: theme.up, downColor: theme.down, borderVisible: false,
        wickUpColor: theme.up, wickDownColor: theme.down,
        priceLineVisible: false, lastValueVisible: true
      });
      state.volume = state.chart.addSeries(library.HistogramSeries, {
        priceFormat: { type: "volume" }, priceScaleId: "volume"
      });
      state.volume.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
      state.observer = new ResizeObserver((entries) => {
        const width = Math.max(1, Math.floor(entries[0].contentRect.width));
        if (state.chart && width !== state.width) {
          state.width = width;
          state.chart.applyOptions({ width });
        }
      });
      state.observer.observe(state.root);
      state.width = Math.max(1, Math.floor(state.root.clientWidth));
      applyData(state, state.pendingData);
    }).catch(() => {
      if (state.status) state.status.textContent = "Chart library unavailable. Check the browser network connection.";
    }).finally(() => { state.loading = false; });
  } else if (state.chart) {
    applyData(state, state.pendingData);
  }

  return () => {
    if (state.observer) state.observer.disconnect();
    if (state.chart) state.chart.remove();
    delete parentElement.__ictStableChart;
  };
};
"""

_COMPONENTS_BY_RUNTIME: dict[int, object] = {}


def render_ohlcv_chart(frame: pd.DataFrame, title: str = "OHLCV", height: int = CHART_HEIGHT,
                       dataset_id: str = "default", reset_id: int = 0,
                       overlays: list | None = None, ict: dict | None = None,
                       drawings: list | None = None,
                       price_decimals: int | None = None) -> None:
    """Mount one stable v2 component and send normalized OHLCV plus overlays."""
    if frame is None or frame.empty:
        return
    # The chart paints with the same palette as the page. Resolved here (not
    # at import) so the component follows the session's theme switch without
    # threading state through every call site.
    from ui import theme as ui_theme

    payload = make_chart_payload(frame, title, dataset_id, reset_id, height,
                                 overlays=overlays, ict=ict, drawings=drawings,
                                 price_decimals=price_decimals,
                                 palette=ui_theme.active_palette())
    # Component definitions belong to a Streamlit runtime. Register once per
    # runtime (including isolated AppTest runtimes), not once per rerun.
    from streamlit.components.v2.get_bidi_component_manager import get_bidi_component_manager

    manager_id = id(get_bidi_component_manager())
    component = _COMPONENTS_BY_RUNTIME.get(manager_id)
    if component is None:
        component = st.components.v2.component(
            "ict_lightweight_chart", html=_HTML, css=_CSS, js=_JS,
        )
        _COMPONENTS_BY_RUNTIME[manager_id] = component
    component(key="ict_lightweight_chart_instance", data=payload,
              width="stretch", height=payload["height"] + 30)
