"""TradingView Lightweight Charts display fed only by normalized app OHLCV."""

import json

LIGHTWEIGHT_CHARTS_VERSION = "5.2.0"


def build_chart_html(frame, title="OHLCV", height=470):
    candles = []
    volumes = []
    for timestamp, row in frame.iterrows():
        t = int(timestamp.timestamp())
        candles.append({"time": t, "open": float(row.open), "high": float(row.high),
                        "low": float(row.low), "close": float(row.close)})
        volumes.append({"time": t, "value": float(row.volume),
                        "color": "rgba(38, 166, 154, 0.45)" if row.close >= row.open else "rgba(239, 83, 80, 0.45)"})
    candles_json = json.dumps(candles, separators=(",", ":"), allow_nan=False)
    volumes_json = json.dumps(volumes, separators=(",", ":"), allow_nan=False)
    title_json = json.dumps(str(title))
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><style>
html,body,#chart{{margin:0;width:100%;height:100%;font:13px sans-serif;background:#fff;color:#222}}
#chart{{height:{int(height)}px}}#status{{padding:8px}}
</style><script src="https://cdn.jsdelivr.net/npm/lightweight-charts@{LIGHTWEIGHT_CHARTS_VERSION}/dist/lightweight-charts.standalone.production.js"></script></head>
<body><div id="status">Loading chart…</div><div id="chart"></div><script>
const candles={candles_json}, volume={volumes_json}, title={title_json};
const status=document.getElementById('status');
if (!window.LightweightCharts) {{ status.textContent='Chart library unavailable. Check the browser network connection.'; }}
else {{
  status.textContent=title+' · OHLCV supplied by the application market-data layer';
  const el=document.getElementById('chart');
  const chart=LightweightCharts.createChart(el,{{width:el.clientWidth,height:{int(height)},layout:{{background:{{type:'solid',color:'#ffffff'}},textColor:'#222',attributionLogo:true}},grid:{{vertLines:{{color:'#f1f3f5'}},horzLines:{{color:'#f1f3f5'}}}},timeScale:{{timeVisible:true,secondsVisible:false}}}});
  const price=chart.addSeries(LightweightCharts.CandlestickSeries,{{upColor:'#26a69a',downColor:'#ef5350',borderVisible:false,wickUpColor:'#26a69a',wickDownColor:'#ef5350'}});
  price.setData(candles);
  const bars=chart.addSeries(LightweightCharts.HistogramSeries,{{priceFormat:{{type:'volume'}},priceScaleId:'volume'}});
  bars.setData(volume);
  bars.priceScale().applyOptions({{scaleMargins:{{top:0.82,bottom:0}}}});
  chart.timeScale().fitContent();
  new ResizeObserver(()=>chart.applyOptions({{width:el.clientWidth}})).observe(el);
}}
</script></body></html>"""


def render_ohlcv_chart(frame, title="OHLCV", height=470):
    """Render the app's validated OHLCV as a chart; never fetch chart-side data."""
    if frame is None or frame.empty:
        return
    import streamlit as st

    st.iframe(build_chart_html(frame, title, height), height=height + 36)
