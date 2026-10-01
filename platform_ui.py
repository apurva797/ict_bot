"""Streamlit presentation layer.

Contains only rendering concerns: the design system, data-health badges, ICT
stage diagnostics, indicator controls, and status-aware messaging. All
calculations live in ``platform_core`` so the same logic can serve a future
HTTP or mobile client.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from platform_core.indicators import INDICATOR_REGISTRY, available_indicators
from platform_core.settings import Settings
from platform_core.status import DataHealth, Status


HEALTH_STYLES = {
    DataHealth.CONNECTED: ("LIVE", "#1f9d55", "Primary provider is streaming fresh candles."),
    DataHealth.DEGRADED: ("BACKUP", "#b7791f", "Using a fallback source or non-live sample data."),
    DataHealth.STALE: ("STALE", "#c05621", "Latest candle is older than the expected interval."),
    DataHealth.UNAVAILABLE: ("OFFLINE", "#c53030", "No usable market data is available."),
}

STATUS_STYLES = {
    Status.SIGNAL_CREATED: ("success", "A valid setup was found and passed risk checks."),
    Status.PAPER_ORDER_CREATED: ("success", "A paper order was created. No broker order was sent."),
    Status.PAPER_ORDER_FILLED: ("success", "A paper order was filled and a position opened."),
    Status.PAPER_POSITION_CLOSED: ("success", "A paper position closed and P&L was booked."),
    Status.NO_VALID_SETUP: ("info", "No valid setup on the latest candle. This is normal."),
    Status.RISK_REJECTED: ("warning", "The setup was valid but risk rules blocked the trade."),
    Status.INSUFFICIENT_DATA: ("warning", "Not enough finalized candles for this calculation."),
    Status.DATA_UNAVAILABLE: ("error", "Market data is unavailable from every provider."),
    Status.STRATEGY_ERROR: ("error", "The strategy could not complete its calculation."),
}


def inject_design_system(settings: Settings) -> None:
    """Inject the shared stylesheet, theme tokens, and mobile layout rules."""
    palette = {
        "bg": "#ffffff" if settings.display.theme != "dark" else "#0b1220",
        "surface": "#f4f7fb" if settings.display.theme != "dark" else "#121a2b",
        "border": "#dfe5ee" if settings.display.theme != "dark" else "#243049",
        "text": "#142033" if settings.display.theme != "dark" else "#e6ecf5",
        "muted": "#5a6b85" if settings.display.theme != "dark" else "#93a4bf",
        "accent": "#1e6bff",
        "up": "#1f9d55",
        "down": "#c53030",
    }
    st.markdown(
        f"""
        <style>
        :root {{
          --pf-bg:{palette['bg']}; --pf-surface:{palette['surface']};
          --pf-border:{palette['border']}; --pf-text:{palette['text']};
          --pf-muted:{palette['muted']}; --pf-accent:{palette['accent']};
        }}
        .block-container {{ padding-top:1.5rem; padding-bottom:4rem; max-width:1500px; }}
        .pf-card {{
          background:var(--pf-surface); border:1px solid var(--pf-border);
          border-radius:12px; padding:0.9rem 1rem; margin-bottom:0.75rem;
        }}
        .pf-card h4 {{ margin:0 0 0.35rem 0; font-size:0.95rem; color:var(--pf-text); }}
        .pf-badge {{
          display:inline-block; padding:2px 9px; border-radius:999px;
          font-size:0.72rem; font-weight:700; letter-spacing:0.04em;
          color:#fff; margin-right:6px;
        }}
        .pf-status {{ font-size:0.8rem; color:var(--pf-muted); margin-top:0.4rem; }}
        .pf-stage {{ display:flex; justify-content:space-between; gap:0.5rem;
          padding:0.3rem 0; border-bottom:1px solid var(--pf-border); font-size:0.84rem; }}
        .pf-stage:last-child {{ border-bottom:none; }}
        .pf-pass {{ color:{palette['up']}; font-weight:700; }}
        .pf-fail {{ color:{palette['down']}; font-weight:700; }}
        .pf-detail {{ color:var(--pf-muted); font-size:0.78rem; }}
        div[data-testid="stMetricValue"] {{ font-variant-numeric:tabular-nums; }}
        .pf-nav {{ display:none; }}
        @media (max-width:768px) {{
          .block-container {{ padding-left:0.6rem; padding-right:0.6rem; }}
          .pf-nav {{
            display:flex; position:fixed; left:0; right:0; bottom:0; z-index:999;
            background:var(--pf-bg); border-top:1px solid var(--pf-border);
            justify-content:space-around; padding:0.45rem 0.25rem calc(0.45rem + env(safe-area-inset-bottom));
          }}
          .pf-nav a {{ color:var(--pf-muted); text-decoration:none; font-size:0.7rem; text-align:center; }}
          div[data-testid="stAppViewBlockContainer"] {{ padding-bottom:4.5rem; }}
          [data-testid="stSidebar"] {{ min-width:0 !important; max-width:100% !important; }}
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_paper_banner(settings: Settings) -> None:
    st.markdown(
        f"""
        <div class="pf-card">
          <span class="pf-badge" style="background:{palette_accent(settings)}">PAPER TRADING</span>
          <span class="pf-badge" style="background:#c53030">LIVE ORDERS DISABLED</span>
          <div class="pf-status">
            Simulation only · {settings.environment} environment · no broker or exchange credentials ·
            24/7 strategy evaluation, trades only when a setup is valid and risk approves it.
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def palette_accent(settings: Settings) -> str:
    return "#1e6bff"


def render_mobile_nav() -> None:
    """Bottom navigation for small screens; a progressive enhancement only."""
    st.markdown(
        """
        <nav class="pf-nav" aria-label="Primary">
          <a href="#markets">Markets</a>
          <a href="#strategies">Strategies</a>
          <a href="#portfolio">Portfolio</a>
        </nav>
        """,
        unsafe_allow_html=True,
    )


def render_data_health(health: DataHealth, source: str, is_live: bool,
                       candles: int | None = None, notes=()) -> None:
    """Always state data health truthfully; never present backup data as live."""
    label, color, description = HEALTH_STYLES.get(
        health, ("UNKNOWN", "#5a6b85", "Data health is unknown."))
    suffix = f" · {candles} candles" if candles else ""
    st.markdown(
        f"""
        <div class="pf-card">
          <span class="pf-badge" style="background:{color}">{label}</span>
          <h4>Data source: {source}</h4>
          <div class="pf-status">{description}{suffix}</div>
          {''.join(f'<div class="pf-status">• {note}</div>' for note in notes)}
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_ict_stages(analysis: dict) -> None:
    """Show the exact ICT decision chain, including the failing condition."""
    stages = analysis.get("stages") or []
    if not stages:
        return
    rows = []
    for stage in stages:
        passed = bool(stage.get("passed"))
        badge = '<span class="pf-pass">PASS</span>' if passed else '<span class="pf-fail">FAIL</span>'
        rows.append(
            f'<div class="pf-stage"><span>{stage.get("name")}</span>{badge}</div>'
            f'<div class="pf-detail">{stage.get("detail", "")}</div>'
        )
    st.markdown(
        f'<div class="pf-card"><h4>ICT decision chain · HTF bias '
        f'{analysis.get("htf_bias", "NEUTRAL")}</h4>{"".join(rows)}</div>',
        unsafe_allow_html=True,
    )


def render_status(status: Status, message: str) -> None:
    """Render a pipeline status with the correct severity for its meaning."""
    level, _ = STATUS_STYLES.get(status, ("info", message))
    renderer = {"success": st.success, "info": st.info, "warning": st.warning,
                "error": st.error}.get(level, st.info)
    renderer(message)


def indicator_choices() -> dict:
    """Display name -> id map for the indicator picker."""
    return {item["name"]: item["id"] for item in available_indicators()}


def render_indicator_controls(key_prefix: str = "chart") -> list:
    """Add / remove / configure / reset indicators and return the selections.

    Every returned selection is a real request the chart computes from the
    loaded candles; nothing here fabricates indicator values.
    """
    catalogue = {item["name"]: item for item in available_indicators()}
    store_key = f"{key_prefix}_indicators"
    chosen = st.session_state.setdefault(store_key, ["EMA", "SUPERTREND", "RSI"])
    selections: list = []
    with st.expander("Indicators", expanded=False):
        picked = st.multiselect(
            "Active indicators", options=sorted(catalogue),
            default=[name for name in chosen if name in catalogue],
            key=f"{key_prefix}_indicator_picker",
            help="Each indicator is calculated from the loaded candles.",
        )
        st.session_state[store_key] = picked
        for name in picked:
            spec = catalogue[name]
            params: dict = {}
            defaults = spec["defaults"]
            if defaults:
                columns = st.columns(min(len(defaults), 3))
                for index, (param, default) in enumerate(defaults.items()):
                    integral = isinstance(default, int) and not isinstance(default, bool)
                    with columns[index % len(columns)]:
                        if integral:
                            value = st.number_input(
                                f"{name} · {param}", min_value=1, value=int(default),
                                step=1, key=f"{key_prefix}_{spec['id']}_{param}")
                        else:
                            value = st.number_input(
                                f"{name} · {param}", value=float(default), step=0.1,
                                key=f"{key_prefix}_{spec['id']}_{param}")
                    params[param] = value
            selections.append({"id": spec["id"], "params": params})
        if st.button("Reset indicators", key=f"{key_prefix}_reset_indicators"):
            st.session_state[store_key] = []
            st.rerun()
    return selections


def render_ict_overlay_toggles(key_prefix: str = "ict") -> dict:
    """Toggle individual ICT structures; only detected structures are drawn."""
    labels = {
        "liquidity": "Liquidity",
        "premium_discount": "Premium / Discount",
        "fvg": "Fair Value Gap",
        "mss": "MSS / BOS",
        "displacement": "Displacement",
        "levels": "Entry / SL / TP",
    }
    store_key = f"{key_prefix}_overlay_toggles"
    current = st.session_state.setdefault(store_key, {key: True for key in labels})
    toggles: dict = {}
    with st.expander("ICT chart overlays", expanded=False):
        columns = st.columns(3)
        for index, (key, label) in enumerate(labels.items()):
            with columns[index % len(columns)]:
                toggles[key] = st.checkbox(
                    label, value=bool(current.get(key, True)),
                    key=f"{key_prefix}_toggle_{key}")
    st.session_state[store_key] = toggles
    return toggles