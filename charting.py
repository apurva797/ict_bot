"""Stable TradingView Lightweight Charts component for normalized app OHLCV."""

from __future__ import annotations

import hashlib
import json

import pandas as pd
import streamlit as st

LIGHTWEIGHT_CHARTS_VERSION = "5.2.0"
CHART_HEIGHT = 470
_REQUIRED_COLUMNS = ("open", "high", "low", "close", "volume")


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


def _chart_rows(frame: pd.DataFrame) -> tuple[list[dict], list[dict]]:
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
            "color": "rgba(38, 166, 154, 0.45)" if row.close >= row.open else "rgba(239, 83, 80, 0.45)",
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
                       reset_id: int = 0, height: int = CHART_HEIGHT) -> dict:
    normalized = normalize_chart_ohlcv(frame)
    candles, volumes = _chart_rows(normalized)
    encoded = json.dumps([candles, volumes], separators=(",", ":"), allow_nan=False)
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    return {
        "candles": candles,
        "volumes": volumes,
        "digest": digest,
        "dataset_id": str(dataset_id),
        "title": str(title),
        "reset_id": int(reset_id),
        "height": max(300, int(height)),
    }


_HTML = '<div class="chart-status" aria-live="polite">Loading chart…</div><div class="chart-root" role="img" aria-label="OHLCV candlestick and volume chart"></div>'
_CSS = f"""
:host {{ display:block; width:100%; min-width:0; }}
.chart-status {{ box-sizing:border-box; height:30px; padding:6px 8px; color:#495057; font:13px sans-serif; overflow:hidden; white-space:nowrap; text-overflow:ellipsis; }}
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

function applyData(state, data) {
  if (!state.chart || !data || !Array.isArray(data.candles)) return;
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
      state.chart = library.createChart(state.root, {
        width: Math.max(1, state.root.clientWidth), height: Math.max(300, Number(state.pendingData.height) || 470),
        layout: { background: { type: "solid", color: "#ffffff" }, textColor: "#222", attributionLogo: true },
        grid: { vertLines: { color: "#f1f3f5" }, horzLines: { color: "#f1f3f5" } },
        timeScale: { timeVisible: true, secondsVisible: false, shiftVisibleRangeOnNewBar: false },
        kineticScroll: { mouse: false, touch: false },
        handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
        handleScale: { axisPressedMouseMove: true, mouseWheel: true, pinch: true }
      });
      state.price = state.chart.addSeries(library.CandlestickSeries, {
        upColor: "#26a69a", downColor: "#ef5350", borderVisible: false,
        wickUpColor: "#26a69a", wickDownColor: "#ef5350",
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
                       dataset_id: str = "default", reset_id: int = 0) -> None:
    """Mount one stable v2 component and send only normalized OHLCV data."""
    if frame is None or frame.empty:
        return
    payload = make_chart_payload(frame, title, dataset_id, reset_id, height)
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
