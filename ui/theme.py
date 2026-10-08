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
# Accent is a deep teal — the platform's own identity, deliberately not a
# generic "exchange blue" — and it never signals P&L, so a coloured surface
# can only mean "interactive / selected", never "the trade is winning".
#
# The full semantic registry the product uses (CSS aliases follow the same
# names with dashes): background (bg), background-elevated (bg_elevated),
# surface, surface-hover, surface-active, border, border-subtle,
# text-primary (text), text-secondary, text-muted (text_faint), positive
# (profit), negative (loss), warning, info, accent — plus radius, shadow and
# animation-duration scales. Components never hard-code a value.

LIGHT = {
    "bg": "#F5F7FB",
    "bg_elevated": "#FFFFFF",
    "surface": "#FFFFFF",
    "surface_alt": "#FAFBFE",
    "surface_sunken": "#EEF1F7",
    "surface_hover": "#F1F4FA",
    "surface_active": "#E8EDF7",
    "border": "#E3E8F2",
    "border_subtle": "#ECF0F7",
    "border_strong": "#C7D0E2",
    "text": "#0D1424",
    "text_secondary": "#566079",
    "text_muted": "#566079",
    "text_faint": "#8891A8",
    "accent": "#147D92",
    "accent_soft": "#E1F3F5",
    "accent_text": "#0D6577",
    "profit": "#0E9F6E",
    "profit_soft": "#E2F6EE",
    "loss": "#DC2626",
    "loss_soft": "#FDECEC",
    "warning": "#B45309",
    "warning_soft": "#FEF3E2",
    "info": "#0369A1",
    "info_soft": "#E4F1FA",
    "shadow": "0 1px 2px rgba(13,20,36,.06), 0 8px 24px rgba(13,20,36,.06)",
    "shadow_lg": "0 2px 6px rgba(13,20,36,.08), 0 18px 48px rgba(13,20,36,.12)",
    "radius": "8px",
    "radius_sm": "6px",
    "radius_pill": "999px",
    "duration": "160ms",
    "duration_slow": "240ms",
}

DARK = {
    "bg": "#070A12",
    "bg_elevated": "#101624",
    "surface": "#101624",
    "surface_alt": "#151C2C",
    "surface_sunken": "#0B0F1A",
    "surface_hover": "#1A2236",
    "surface_active": "#202A44",
    "border": "#1F2942",
    "border_subtle": "#182036",
    "border_strong": "#2E3A57",
    "text": "#E9EEF8",
    "text_secondary": "#A7B1C9",
    "text_muted": "#98A3BE",
    "text_faint": "#6C7896",
    "accent": "#3FC0D0",
    "accent_soft": "#0F3540",
    "accent_text": "#7FE1EA",
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
    "duration": "160ms",
    "duration_slow": "240ms",
}

THEMES = {"light": LIGHT, "dark": DARK}

# Inter for the UI (excellent digit shapes) and IBM Plex Mono for code and
# raw figures. Both load as webfonts with system fallbacks, so an offline
# browser degrades to a native UI font instead of an unreadable serif.
FONT_STACK = ('Inter, "Segoe UI", "Helvetica Neue", Arial, sans-serif')

MONO_STACK = ('"IBM Plex Mono", "SFMono-Regular", "Cascadia Mono", '
              'Menlo, Consolas, monospace')

WEBFONT_IMPORT = ('@import url("https://fonts.googleapis.com/css2?'
                  'family=Inter:wght@400;500;600;700&'
                  'family=IBM+Plex+Mono:wght@400;500;600&display=swap");')

# Display density. Traders scale information density to their screens, so the
# multiplier drives card padding, section rhythm, grid gaps and table cells
# through one CSS custom property. Preferences persist for the session.
DENSITY_LEVELS = {"Comfortable": 1.0, "Compact": 0.85, "Dense": 0.7}
DEFAULT_DENSITY = "Comfortable"

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


def active_theme(settings=None) -> str:
    """The theme name in force: session switch → configured value → auto.

    The Settings screen flips ``ui_theme`` for the session only; it is
    presentation state and never touches a trading decision or the server
    configuration.
    """
    try:
        override = st.session_state.get("ui_theme")
    except Exception:  # pragma: no cover - state outside a Streamlit run
        override = None
    if override in THEMES:
        return override
    configured = getattr(getattr(settings, "display", None), "theme", "auto")
    return configured or "auto"


def active_palette(settings=None) -> dict:
    """The palette actually painted this run (see :func:`active_theme`)."""
    return resolve_palette(active_theme(settings))


def density_value() -> float:
    """The active display-density multiplier (see :data:`DENSITY_LEVELS`)."""
    try:
        name = st.session_state.get("ui_density") or DEFAULT_DENSITY
    except Exception:  # pragma: no cover - state outside a Streamlit run
        return float(DENSITY_LEVELS[DEFAULT_DENSITY])
    return float(DENSITY_LEVELS.get(name, DENSITY_LEVELS[DEFAULT_DENSITY]))


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
  transition: transform var(--ui-duration, 160ms) ease,
              border-color var(--ui-duration, 160ms) ease,
              background var(--ui-duration, 160ms) ease,
              color var(--ui-duration, 160ms) ease;
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
* { scrollbar-width: thin; }
*::-webkit-scrollbar { width: 10px; height: 10px; }
*::-webkit-scrollbar-thumb {
  background: var(--ui-border-strong); border-radius: 999px;
  border: 2px solid transparent; background-clip: padding-box;
}
*::-webkit-scrollbar-track { background: transparent; }

/* Keyboard users always see where they are. */
:focus-visible {
  outline: 2px solid var(--ui-accent);
  outline-offset: 2px;
  border-radius: 3px;
}
.stButton > button:focus-visible, [data-baseweb="tab"]:focus-visible {
  outline-offset: 1px;
}
::selection { background: var(--ui-accent-soft); color: var(--ui-accent-text); }

/* Financial figures align on the digit everywhere they appear. */
.ui-value, .ui-mono, .ui-kv-v, .ui-pill,
div[data-testid="stMetricValue"], .ui-table td, .ui-table th,
.terminal-price, .terminal-value, .terminal-change {
  font-variant-numeric: tabular-nums;
  font-feature-settings: "tnum" 1;
}

/* ---------- Terminal top bar ---------- */
.ui-shell-topbar {
  display: grid;
  grid-template-columns: minmax(0, 1.1fr) minmax(0, auto) minmax(0, 1fr);
  align-items: center;
  gap: .75rem 1.25rem;
  padding: .65rem 0 .8rem;
  border-bottom: 1px solid var(--ui-border);
  margin-bottom: .9rem;
}
.ui-shell-identity { display: flex; align-items: center; gap: .7rem; min-width: 0; }
.ui-shell-mark {
  display: grid; place-items: center;
  width: 34px; height: 34px; flex: none;
  border-radius: var(--ui-radius-sm);
  background: var(--ui-accent-soft); color: var(--ui-accent-text);
  font-weight: 760; font-size: .92rem; letter-spacing: .02em;
}
.ui-shell-name { color: var(--ui-text); font-size: .98rem; font-weight: 720; line-height: 1.1; }
.ui-shell-tagline {
  color: var(--ui-text-faint); font-size: .6rem; font-weight: 660;
  letter-spacing: .14em; text-transform: uppercase; margin-top: .15rem;
}
.ui-shell-center {
  display: flex; align-items: center; justify-content: center;
  gap: .5rem; flex-wrap: wrap;
}
.ui-shell-actions {
  display: flex; align-items: center; justify-content: flex-end;
  gap: .5rem; flex-wrap: wrap;
}
.ui-shell-hint {
  color: var(--ui-text-faint); font-size: .64rem; font-weight: 640;
  letter-spacing: .06em; border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius-sm); padding: .18rem .42rem;
}
@media (max-width: 900px) {
  .ui-shell-topbar { grid-template-columns: minmax(0, 1fr); }
  .ui-shell-center, .ui-shell-actions { justify-content: flex-start; }
}
@media (max-width: 520px) {
  .ui-shell-center .ui-pill:nth-child(n + 3) { display: none; }
  .ui-shell-name { font-size: .88rem; }
  .ui-shell-tagline { font-size: .52rem; letter-spacing: .1em; }
  .ui-shell-topbar { gap: .35rem; padding-bottom: .55rem; margin-bottom: .55rem; }
}

/* ---------- Command palette / shortcuts dialog ---------- */
[data-testid="stDialog"] {
  border: 1px solid var(--ui-border-strong);
  border-radius: calc(var(--ui-radius) + 4px);
  background: var(--ui-surface);
  box-shadow: var(--ui-shadow-lg);
  color: var(--ui-text);
}
.cmdk-title {
  font-size: .68rem; font-weight: 700; letter-spacing: .12em;
  text-transform: uppercase; color: var(--ui-text-faint); margin-bottom: .35rem;
}
.cmdk-item {
  display: flex; align-items: center; justify-content: space-between; gap: .6rem;
  padding: .5rem .6rem; border-radius: var(--ui-radius-sm);
  border: 1px solid var(--ui-border); background: var(--ui-surface-alt);
  margin-bottom: .4rem; font-size: .84rem;
}
.cmdk-item--active { border-color: var(--ui-accent); background: var(--ui-accent-soft); }
.cmdk-kind {
  font-size: .62rem; font-weight: 700; letter-spacing: .08em;
  text-transform: uppercase; color: var(--ui-text-faint);
}
.cmdk-keys { display: flex; gap: .3rem; align-items: center; }
.cmdk-key {
  font-family: var(--ui-mono); font-size: .66rem; color: var(--ui-text-muted);
  border: 1px solid var(--ui-border-strong); border-bottom-width: 2px;
  border-radius: 4px; padding: .05rem .35rem; background: var(--ui-surface-sunken);
}

/* ---------- Toasts: quiet, bottom-corner confirmation ---------- */
[data-testid="stToast"] {
  border: 1px solid var(--ui-border-strong);
  background: var(--ui-surface-elevated, var(--ui-bg-elevated));
  box-shadow: var(--ui-shadow-lg);
}
[data-testid="stToast"] [data-testid="stToastContent"] { color: var(--ui-text); }

/* ---------- Segmented controls (nav, windows, palette filters) ---------- */
[data-testid="stButtonGroup"] {
  border: 1px solid var(--ui-border);
  background: var(--ui-surface-sunken);
  border-radius: var(--ui-radius-sm);
  padding: 3px; gap: 3px;
}
[data-testid="stButtonGroup"] [role="radio"],
[data-testid="stButtonGroup"] button {
  border-radius: calc(var(--ui-radius-sm) - 2px) !important;
  font-weight: 600 !important;
  transition: background var(--ui-duration, 160ms) ease,
              color var(--ui-duration, 160ms) ease;
}
[data-testid="stButtonGroup"] [role="radio"]:hover,
[data-testid="stButtonGroup"] button:hover {
  background: var(--ui-surface-hover) !important;
}
[data-testid="stButtonGroup"] [aria-checked="true"],
[data-testid="stButtonGroup"] button[aria-pressed="true"] {
  background: var(--ui-surface) !important;
  color: var(--ui-accent-text) !important;
  box-shadow: var(--ui-shadow);
  border-color: var(--ui-border-strong) !important;
}
"""


def _component_rules() -> str:
    """Classes emitted by :mod:`ui.components`."""
    return """
/* ---------- Cards and grids ---------- */
.ui-card {
  background: var(--ui-surface); border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius);
  padding: calc(1rem * var(--ui-density, 1)) calc(1.05rem * var(--ui-density, 1));
  box-shadow: var(--ui-shadow);
  transition: border-color var(--ui-duration, 160ms) ease,
              box-shadow var(--ui-duration, 160ms) ease,
              background var(--ui-duration, 160ms) ease;
}
.ui-card--flat { box-shadow: none; background: var(--ui-surface-alt); }
.ui-card--sunken { box-shadow: none; background: var(--ui-surface-sunken); }
.ui-card--accent { border-left: 3px solid var(--ui-accent); }
.ui-card--profit { border-left: 3px solid var(--ui-profit); }
.ui-card--loss { border-left: 3px solid var(--ui-loss); }
.ui-card--interactive { transition: border-color var(--ui-duration, 160ms) ease, box-shadow var(--ui-duration, 160ms) ease; }
.ui-card--interactive:hover { border-color: var(--ui-border-strong); box-shadow: var(--ui-shadow-lg); }
.ui-card--interactive:active { transform: translateY(1px); }

.ui-grid {
  display: grid;
  gap: calc(.75rem * var(--ui-density, 1));
}
.ui-grid--2 { grid-template-columns: repeat(2, minmax(0, 1fr)); }
.ui-grid--3 { grid-template-columns: repeat(3, minmax(0, 1fr)); }
.ui-grid--4 { grid-template-columns: repeat(4, minmax(0, 1fr)); }
@media (max-width: 900px) { .ui-grid--3, .ui-grid--4 { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 560px) { .ui-grid--2, .ui-grid--3, .ui-grid--4 { grid-template-columns: minmax(0, 1fr); } }

.ui-section-head {
  display: flex; align-items: baseline; justify-content: space-between;
  gap: .75rem;
  margin: calc(1.35rem * var(--ui-density, 1)) 0 calc(.6rem * var(--ui-density, 1));
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
  color: var(--ui-text-faint); font-weight: 680;
  padding: calc(.5rem * var(--ui-density, 1)) calc(.6rem * var(--ui-density, 1));
  white-space: nowrap;
}
.ui-table td {
  padding: calc(.6rem * var(--ui-density, 1)) calc(.6rem * var(--ui-density, 1));
  border-top: 1px solid var(--ui-border); font-variant-numeric: tabular-nums;
}
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
    /* One slot per route (Home, Markets, Trade, Portfolio, Research,
       Settings): six items must wrap to no second row. */
    grid-template-columns: repeat(6, minmax(0, 1fr));
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


def _stylesheet(palette: dict, density: float) -> str:
    """Assemble the full stylesheet for one palette and density."""
    return f"""{WEBFONT_IMPORT}
:root {{
{_token_css(palette)}
  --ui-font: {FONT_STACK};
  --ui-mono: {MONO_STACK};
  --ui-touch: {MIN_TOUCH_PX}px;
  --ui-nav-height: {MIN_TOUCH_PX + 26}px;
  --ui-density: {density};
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
    palette = active_palette(settings)
    st.markdown(f"<style>{_stylesheet(palette, density_value())}</style>",
                unsafe_allow_html=True)
    return palette