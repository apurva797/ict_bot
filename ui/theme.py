"""Original visual identity for the trading platform.

Design intent
-------------
A single consumer-grade trading surface on top of a quantitative engine:
generous whitespace on mobile, dense but scannable panels on desktop, and
colour used only where it carries meaning (profit, loss, status).

The palette is this project's own. Tokens are exposed to CSS as custom
properties so components never hard-code a hex value, and every component
reads from :data:`LIGHT` / :data:`DARK` so a theme switch needs no re-render.
"""

from __future__ import annotations

import streamlit as st

# --------------------------------------------------------------------------
# Design tokens
# --------------------------------------------------------------------------
# Accent is a deep indigo-violet. It is deliberately not a generic
# "exchange blue" and never signals P&L, so a coloured surface can only mean
# "interactive / selected", never "the trade is winning".

LIGHT = {
    "bg": "#F6F7FB",
    "surface": "#FFFFFF",
    "surface_alt": "#FAFBFE",
    "surface_sunken": "#F0F2F8",
    "border": "#E4E8F2",
    "border_strong": "#CFD6E6",
    "text": "#0E1424",
    "text_muted": "#5B6580",
    "text_faint": "#8A93AB",
    "accent": "#147D92",
    "accent_soft": "#E3F4F5",
    "accent_text": "#0E6678",
    "profit": "#0E9F6E",
    "profit_soft": "#E3F6EE",
    "loss": "#DC2626",
    "loss_soft": "#FDECEC",
    "warning": "#B45309",
    "warning_soft": "#FEF3E2",
    "info": "#0369A1",
    "info_soft": "#E4F1FA",
    "shadow": "0 1px 2px rgba(14,20,36,.06), 0 8px 24px rgba(14,20,36,.06)",
    "shadow_lg": "0 2px 6px rgba(14,20,36,.08), 0 18px 48px rgba(14,20,36,.12)",
    "radius": "8px",
    "radius_sm": "6px",
    "radius_pill": "999px",
}

DARK = {
    "bg": "#080B14",
    "surface": "#111726",
    "surface_alt": "#161D2F",
    "surface_sunken": "#0C1120",
    "border": "#222B41",
    "border_strong": "#313C57",
    "text": "#EAEEF9",
    "text_muted": "#96A0BA",
    "text_faint": "#6B7691",
    "accent": "#47C3D1",
    "accent_soft": "#12343D",
    "accent_text": "#7DE0E8",
    "profit": "#2DD4A3",
    "profit_soft": "#0C2A25",
    "loss": "#FF6B6B",
    "loss_soft": "#2C1418",
    "warning": "#F5A524",
    "warning_soft": "#2B1F0C",
    "info": "#4EA8F0",
    "info_soft": "#0D2033",
    "shadow": "0 1px 2px rgba(0,0,0,.4), 0 8px 24px rgba(0,0,0,.35)",
    "shadow_lg": "0 2px 6px rgba(0,0,0,.45), 0 18px 48px rgba(0,0,0,.5)",
    "radius": "8px",
    "radius_sm": "6px",
    "radius_pill": "999px",
}

THEMES = {"light": LIGHT, "dark": DARK}

FONT_STACK = '"IBM Plex Sans", "Segoe UI", sans-serif'

MONO_STACK = ('"SF Mono", "JetBrains Mono", "Roboto Mono", Menlo, Consolas, monospace')

# Touch-target floor from WCAG 2.5.5 / Apple HIG. Mobile-first means a control
# is never smaller than this, whatever the viewport.
MIN_TOUCH_PX = 44


def resolve_palette(theme: str = "auto") -> dict:
    """Return the token set for a theme name.

    ``auto`` follows the platform's own theme setting rather than a CSS media
    query, so the rendered markup and the chart component can never disagree
    about which palette is active.
    """
    key = (theme or "auto").strip().lower()
    if key in {"light", "dark"}:
        return THEMES[key]
    try:
        system = st.get_option("theme.base") or "light"
    except Exception:
        system = "light"
    return THEMES["dark" if str(system).strip().lower() == "dark" else "light"]


def _token_css(palette: dict) -> str:
    return "\n".join(
        f"  --ui-{name.replace('_', '-')}: {value};"
        for name, value in palette.items()
    )


def _base_rules() -> str:
    """App frame, typography, and widget restyling.

    Layout uses flex/grid so one set of components reflows from a 360px phone
    to a wide desktop terminal with no duplicated component code.
    """
    return """
html, body, [class*="css"] {
  font-family: var(--ui-font);
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}

.stApp { background: var(--ui-bg); }
.block-container, [data-testid="stAppViewBlockContainer"] > .main { background: var(--ui-bg); }
.block-container {
  padding-top: 1.1rem;
  padding-bottom: calc(var(--ui-nav-height) + 2.5rem);
  max-width: 1560px;
}
@media (min-width: 1400px) { .block-container { padding-left: 2rem; padding-right: 2rem; } }

.ui-brand { color: var(--ui-text); font-size: 1.05rem; font-weight: 760; letter-spacing: .04em; }
.ui-brand span { display: block; color: var(--ui-text-faint); font-size: .58rem; letter-spacing: .13em; margin-top: .15rem; }
.ui-topbar { display: flex; align-items: end; justify-content: space-between; gap: 1rem; padding-bottom: .85rem; border-bottom: 1px solid var(--ui-border); margin-bottom: .85rem; }
.ui-eyebrow { color: var(--ui-text-faint); font-size: .62rem; font-weight: 700; letter-spacing: .14em; }
.ui-shell-title { color: var(--ui-text); font-size: 1.25rem; font-weight: 720; margin-top: .2rem; }
.ui-topbar-meta { color: var(--ui-text-muted); font-size: .68rem; letter-spacing: .08em; }
.terminal-header { display: grid; grid-template-columns: 1.35fr 1fr 1fr .7fr auto; align-items: center; gap: 1rem; padding: .7rem .85rem; margin-bottom: .65rem; background: var(--ui-surface); border: 1px solid var(--ui-border); border-radius: var(--ui-radius-sm); }
.terminal-symbol { font-size: 1.08rem; font-weight: 720; }
.terminal-price, .terminal-value { font-size: 1rem; font-weight: 680; font-variant-numeric: tabular-nums; }
.terminal-change { font-size: .92rem; font-weight: 650; font-variant-numeric: tabular-nums; }
.terminal-market-status { justify-self: end; }
.terminal-watch-row { display: flex; align-items: center; justify-content: space-between; gap: .4rem; padding: .6rem .55rem; margin-bottom: .3rem; border: 1px solid transparent; border-bottom-color: var(--ui-border); font-size: .78rem; }
.terminal-watch-row--selected { background: var(--ui-accent-soft); border-color: var(--ui-accent); border-radius: var(--ui-radius-sm); }
.terminal-watch-price { display: grid; justify-items: end; gap: .15rem; font-variant-numeric: tabular-nums; }
.terminal-bottom { margin-top: .8rem; border-top: 1px solid var(--ui-border); padding-top: .2rem; }
@media (max-width: 1100px) { .terminal-header { grid-template-columns: repeat(3, minmax(0, 1fr)); } .terminal-market-status { justify-self: start; } }
@media (max-width: 760px) { .terminal-header { grid-template-columns: repeat(2, minmax(0, 1fr)); } }

/* Streamlit ships oversized headings that fight this product's hierarchy. */
h1, h2, h3, h4, h5 { color: var(--ui-text); letter-spacing: -.02em; font-weight: 680; }
h1 { font-size: 1.5rem !important; }
h2 { font-size: 1.2rem !important; }
h3 { font-size: 1.02rem !important; }
h4 { font-size: .92rem !important; }
p, span, label, div { color: var(--ui-text); }
a { color: var(--ui-accent); }

div[data-testid="stMetricValue"] {
  font-variant-numeric: tabular-nums; font-weight: 640; letter-spacing: -.015em;
}
div[data-testid="stMetricLabel"] { font-weight: 500; }
div[data-testid="stMetric"] {
  background: var(--ui-surface); border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius-sm); padding: .75rem .9rem;
}

/* Tabs read as a swipeable strip on mobile and a rail on desktop. */
[data-baseweb="tab-list"] {
  gap: .25rem; background: transparent;
  border-bottom: 1px solid var(--ui-border);
  overflow-x: auto; scrollbar-width: none;
}
[data-baseweb="tab-list"]::-webkit-scrollbar { display: none; }
[data-baseweb="tab"] {
  font-weight: 560; padding: .5rem .85rem;
  min-height: var(--ui-touch); white-space: nowrap;
}
[data-baseweb="tab-highlight"] { background: var(--ui-accent); }
[data-baseweb="tab-border"] { background: var(--ui-accent); }
"""


def _widget_rules() -> str:
    """Buttons, inputs, frames, and scrollbars."""
    return """
/* Every interactive control clears the touch-target floor. */
.stButton > button, .stDownloadButton > button, .stFormSubmitButton > button {
  min-height: var(--ui-touch);
  border-radius: var(--ui-radius-sm);
  border: 1px solid var(--ui-border-strong);
  background: var(--ui-surface); color: var(--ui-text); font-weight: 580;
  transition: transform .12s ease, border-color .12s ease;
}
.stButton > button:hover, .stDownloadButton > button:hover {
  border-color: var(--ui-accent); color: var(--ui-accent);
}
.stButton > button:active { transform: translateY(1px); }
.stButton > button[kind="primary"], .stFormSubmitButton > button[kind="primary"] {
  background: var(--ui-accent); border-color: var(--ui-accent); color: #fff;
}

/* Inputs inherit the card language instead of browser defaults. */
[data-testid="stTextInputRoot"] input,
[data-testid="stNumberInputRoot"] input,
[data-baseweb="select"] > div,
[data-baseweb="input"] {
  border-radius: var(--ui-radius-sm) !important;
  background: var(--ui-surface) !important;
  border-color: var(--ui-border-strong) !important;
  color: var(--ui-text) !important;
}

/* Charts and dataframes share the surface language of everything else. */
[data-testid="stDataFrame"], [data-testid="stPlotlyChart"] {
  border: 1px solid var(--ui-border); border-radius: var(--ui-radius-sm); overflow: hidden;
}
[data-testid="stExpander"] {
  border: 1px solid var(--ui-border); border-radius: var(--ui-radius-sm);
  background: var(--ui-surface);
}

* { scrollbar-color: var(--ui-border-strong) transparent; }
"""


def _component_rules() -> str:
    """Classes emitted by :mod:`ui.components`."""
    return """
/* ---------- Cards and grids ---------- */
.ui-card {
  background: var(--ui-surface); border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius); padding: 1rem 1.05rem; box-shadow: var(--ui-shadow);
}
.ui-card--flat { box-shadow: none; background: var(--ui-surface-alt); }
.ui-card--sunken { box-shadow: none; background: var(--ui-surface-sunken); }
.ui-card--accent { border-left: 3px solid var(--ui-accent); }
.ui-card--profit { border-left: 3px solid var(--ui-profit); }
.ui-card--loss { border-left: 3px solid var(--ui-loss); }
.ui-card--interactive { transition: border-color .15s ease, box-shadow .15s ease; }
.ui-card--interactive:hover { border-color: var(--ui-border-strong); box-shadow: var(--ui-shadow-lg); }

.ui-grid { display: grid; gap: .75rem; }
.ui-grid--2 { grid-template-columns: repeat(2, minmax(0, 1fr)); }
.ui-grid--3 { grid-template-columns: repeat(3, minmax(0, 1fr)); }
.ui-grid--4 { grid-template-columns: repeat(4, minmax(0, 1fr)); }
@media (max-width: 900px) { .ui-grid--3, .ui-grid--4 { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 560px) { .ui-grid--2, .ui-grid--3, .ui-grid--4 { grid-template-columns: minmax(0, 1fr); } }

.ui-section-head {
  display: flex; align-items: baseline; justify-content: space-between;
  gap: .75rem; margin: 1.35rem 0 .6rem;
}
.ui-section-title {
  font-size: .74rem; font-weight: 700; letter-spacing: .1em;
  text-transform: uppercase; color: var(--ui-text-faint); margin: 0;
}
.ui-section-note { font-size: .78rem; color: var(--ui-text-muted); }

/* ---------- Type ---------- */
.ui-label {
  font-size: .7rem; font-weight: 640; letter-spacing: .07em;
  text-transform: uppercase; color: var(--ui-text-faint);
}
.ui-value {
  font-size: 1.32rem; font-weight: 700; letter-spacing: -.02em;
  color: var(--ui-text); font-variant-numeric: tabular-nums;
}
.ui-value--sm { font-size: 1.05rem; }
.ui-value--lg { font-size: 1.75rem; }
.ui-sub { font-size: .78rem; color: var(--ui-text-muted); }
.ui-mono { font-family: var(--ui-mono); font-variant-numeric: tabular-nums; }
.ui-pos { color: var(--ui-profit); }
.ui-neg { color: var(--ui-loss); }
.ui-flat { color: var(--ui-text-muted); }
.ui-truncate { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

/* ---------- Pills ---------- */
.ui-pill {
  display: inline-flex; align-items: center; gap: .35rem;
  padding: .2rem .6rem; border-radius: var(--ui-radius-pill);
  font-size: .7rem; font-weight: 680; letter-spacing: .04em;
  background: var(--ui-surface-sunken); color: var(--ui-text-muted);
  border: 1px solid var(--ui-border);
}
.ui-pill--accent { background: var(--ui-accent-soft); color: var(--ui-accent-text); border-color: transparent; }
.ui-pill--profit { background: var(--ui-profit-soft); color: var(--ui-profit); border-color: transparent; }
.ui-pill--loss { background: var(--ui-loss-soft); color: var(--ui-loss); border-color: transparent; }
.ui-pill--warning { background: var(--ui-warning-soft); color: var(--ui-warning); border-color: transparent; }
.ui-pill--info { background: var(--ui-info-soft); color: var(--ui-info); border-color: transparent; }
.ui-dot { width: 7px; height: 7px; border-radius: 50%; background: currentColor; flex: none; }
.ui-dot--live { animation: ui-pulse 2s ease-in-out infinite; }
@keyframes ui-pulse { 0%, 100% { opacity: 1; } 50% { opacity: .35; } }
@media (prefers-reduced-motion: reduce) { .ui-dot--live { animation: none; } }
"""


def _state_rules() -> str:
    """Empty, loading, row, table, and sticky-tradebar classes."""
    return """
.ui-empty {
  border: 1px dashed var(--ui-border-strong); border-radius: var(--ui-radius);
  padding: 1.75rem 1.25rem; text-align: center; background: var(--ui-surface-alt);
}
.ui-empty-title { font-weight: 640; color: var(--ui-text); margin-bottom: .2rem; }
.ui-empty-body { font-size: .82rem; color: var(--ui-text-muted); }

.ui-skeleton {
  background: linear-gradient(90deg, var(--ui-surface-sunken) 25%, var(--ui-border) 50%, var(--ui-surface-sunken) 75%);
  background-size: 200% 100%; animation: ui-shimmer 1.4s linear infinite;
  border-radius: var(--ui-radius-sm);
}
@keyframes ui-shimmer { 0% { background-position: 200% 0; } 100% { background-position: -200% 0; } }
@media (prefers-reduced-motion: reduce) { .ui-skeleton { animation: none; } }

.ui-row { display: flex; align-items: center; justify-content: space-between; gap: .75rem; }
.ui-row + .ui-row { margin-top: .5rem; padding-top: .5rem; border-top: 1px solid var(--ui-border); }
.ui-kv { display: flex; align-items: baseline; justify-content: space-between; gap: .5rem; font-size: .82rem; }
.ui-kv-k { color: var(--ui-text-muted); }
.ui-kv-v { font-weight: 600; font-variant-numeric: tabular-nums; }

/* Sticky trade controls sit above the mobile nav instead of scrolling away. */
.ui-tradebar {
  position: sticky; bottom: calc(var(--ui-nav-height) + .5rem); z-index: 60;
  background: var(--ui-surface); border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius); padding: .7rem .85rem; box-shadow: var(--ui-shadow-lg);
}
@media (max-width: 768px) { .ui-tradebar { bottom: calc(var(--ui-nav-height) + .35rem); } }

.ui-scroll-x { overflow-x: auto; -webkit-overflow-scrolling: touch; scrollbar-width: none; }
.ui-scroll-x::-webkit-scrollbar { display: none; }
.ui-table { width: 100%; border-collapse: collapse; font-size: .84rem; }
.ui-table th {
  text-align: left; font-size: .68rem; letter-spacing: .08em; text-transform: uppercase;
  color: var(--ui-text-faint); font-weight: 680; padding: .5rem .6rem; white-space: nowrap;
}
.ui-table td { padding: .6rem; border-top: 1px solid var(--ui-border); font-variant-numeric: tabular-nums; }
.ui-table tbody tr:hover { background: var(--ui-surface-alt); }
"""


def _responsive_rules() -> str:
    """Sidebar rail on desktop, bottom navigation on mobile.

    The bottom bar is a non-interactive HTML mirror of the real segmented
    control rendered above it: it only reflects the active route and never
    pretends to be clickable, so accessibility stays with the real widget.
    """
    return """
/* ---------- Sidebar (desktop navigation) ---------- */
[data-testid="stSidebar"] {
  background: var(--ui-surface); border-right: 1px solid var(--ui-border);
}
[data-testid="stSidebar"] .block-container { padding-bottom: 2rem; }
[data-testid="stSidebarNav"] a { border-radius: var(--ui-radius-sm); }
[data-testid="stSidebarNav"] a:hover { background: var(--ui-surface-alt); }

/* Hidden on desktop, where the sidebar carries navigation instead. */
.ui-bottom-nav { display: none; }

@media (max-width: 768px) {
  .ui-bottom-nav {
    display: grid;
    grid-template-columns: repeat(5, minmax(0, 1fr));
    position: fixed; left: 0; right: 0; bottom: 0; z-index: 999;
    background: var(--ui-surface);
    border-top: 1px solid var(--ui-border);
    padding: .3rem .2rem calc(.3rem + env(safe-area-inset-bottom));
  }
  .ui-nav-item {
    display: flex; flex-direction: column; align-items: center; justify-content: center;
    gap: 2px; min-height: var(--ui-touch);
    color: var(--ui-text-faint); font-size: .63rem; font-weight: 600;
    letter-spacing: .01em; border-radius: var(--ui-radius-sm);
  }
  .ui-nav-item svg { width: 21px; height: 21px; flex: none; }
  .ui-nav-item--active { color: var(--ui-accent); font-weight: 680; }

  /* The sidebar becomes a full-screen drawer so controls are not crammed
     into a permanently collapsed rail on a phone. */
  [data-testid="stSidebar"] { min-width: 0 !important; max-width: 100% !important; }
  [data-testid="stSidebar"][aria-expanded="true"] { width: 100% !important; }
}

@media (min-width: 769px) { .block-container { padding-bottom: 2.5rem; } }

/* This product owns its frame, so Streamlit's chrome is suppressed. */
footer { visibility: hidden; }
#MainMenu, header [data-testid="stStatusWidget"] { visibility: hidden; }
"""


def _stylesheet(palette: dict) -> str:
    """Assemble the full stylesheet for one palette."""
    return f""":root {{
{_token_css(palette)}
  --ui-font: {FONT_STACK};
  --ui-mono: {MONO_STACK};
  --ui-touch: {MIN_TOUCH_PX}px;
  --ui-nav-height: {MIN_TOUCH_PX + 26}px;
}}
{_base_rules()}
{_widget_rules()}
{_component_rules()}
{_state_rules()}
{_responsive_rules()}
"""


def inject_design_system(settings=None) -> dict:
    """Inject the stylesheet for the active theme and return its tokens.

    Returning the palette lets callers align non-CSS surfaces (for example the
    chart component) to the same tokens without re-reading the setting.
    """
    theme = getattr(getattr(settings, "display", None), "theme", "auto") or "auto"
    palette = resolve_palette(theme)
    st.markdown(f"<style>{_stylesheet(palette)}</style>", unsafe_allow_html=True)
    return palette