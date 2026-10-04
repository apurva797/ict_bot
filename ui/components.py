"""Presentational components.

Every helper here returns HTML that :mod:`ui.theme` styles through CSS custom
properties, so a palette change never requires re-rendering markup. Components
are pure string builders: they hold no state and never touch the paper account,
market data, or risk engines.
"""

from __future__ import annotations

import html
import math
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

import streamlit as st

# Semantics for P&L colouring. The accent colour is never used to express
# profit or loss, so a coloured surface has exactly one possible meaning.
_TONE_CLASS = {"pos": "ui-pos", "neg": "ui-neg", "flat": "ui-flat"}


def esc(value: Any) -> str:
    """Escape any value for interpolation into HTML."""
    return html.escape("" if value is None else str(value), quote=True)


def tone_class(value: float | int | None, *, epsilon: float = 0.0) -> str:
    """Map a signed number to its colour class; near-zero reads as neutral."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return _TONE_CLASS["flat"]
    if not math.isfinite(number) or abs(number) <= epsilon:
        return _TONE_CLASS["flat"]
    return _TONE_CLASS["pos" if number > 0 else "neg"]


def money(value: float | int | None, currency: str = "$", decimals: int = 2,
          *, signed: bool = True, compact: bool = False) -> str:
    """Format a monetary amount.

    ``signed`` is on by default because in a trading app an unsigned number next
    to a P&L label is ambiguous; the explicit ``+``/``-`` removes the guess.
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "--"
    if not math.isfinite(number):
        return "--"
    if compact and abs(number) >= 100_000:
        return f"{'-' if number < 0 else ''}{currency}{abs(number) / 1000:,.1f}k"
    prefix = ""
    if signed and number > 0:
        prefix = "+"
    return f"{prefix}{currency}{number:,.{decimals}f}"


def percent(value: float | int | None, decimals: int = 2, *, signed: bool = True) -> str:
    """Format a percentage; ``None`` renders as an em dash, never ``0``."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "--"
    if not math.isfinite(number):
        return "--"
    return f"{number:+.{decimals}f}%" if signed else f"{number:.{decimals}f}%"


def ratio(value: float | int | None, decimals: int = 2, suffix: str = "") -> str:
    """Format a bounded metric such as a ratio or R multiple."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "--"
    if not math.isfinite(number):
        return "∞" if number > 0 else "--"
    return f"{number:,.{decimals}f}{suffix}"


def _metric_or_dash(value: float | int | None, formatter, *args, **kwargs) -> str:
    """Render a value, or an em dash when the sample is too small.

    A missing Sharpe ratio must never read as ``0.00``; that is a real number
    with a real meaning and would misrepresent an under-sized sample.
    """
    if value is None:
        return "--"
    try:
        if not math.isfinite(float(value)):
            return "∞"
    except (TypeError, ValueError):
        return "--"
    return formatter(value, *args, **kwargs)


def html_block(markup: str) -> None:
    """Emit pre-built HTML into the Streamlit tree."""
    st.markdown(markup, unsafe_allow_html=True)


def grid(columns: int, *cards: str) -> str:
    """Compose a responsive CSS grid of already-built cards."""
    if not cards:
        return ""
    count = max(2, min(int(columns), 4))
    return f'<div class="ui-grid ui-grid--{count}">{"".join(cards)}</div>'


def card(body: str, *, variant: str = "", interactive: bool = False) -> str:
    """Wrap content in a surface. ``variant`` selects a semantic edge colour."""
    classes = ["ui-card"]
    if variant:
        classes.append(f"ui-card--{variant}")
    elif interactive:
        classes.append("ui-card--flat")
    if interactive:
        classes.append("ui-card--interactive")
    class_names = " ".join(classes)
    return f'<div class="{class_names}">{body}</div>'


def section_head(title: str, note: str = "") -> str:
    """Uppercase section label with an optional right-aligned note."""
    tail = f'<div class="ui-section-note">{esc(note)}</div>' if note else ""
    return (
        f'<div class="ui-section-head"><h3 class="ui-section-title">{esc(title)}</h3>'
        f"{tail}</div>"
    )


def pill(text: str, tone: str = "", *, dot: bool = False, live: bool = False) -> str:
    """Compact status chip. ``dot`` adds a state indicator for status pills."""
    classes = ["ui-pill"]
    if tone:
        classes.append(f"ui-pill--{tone}")
    marker = ""
    if dot:
        marker = f'<span class="ui-dot{" ui-dot--live" if live else ""}"></span>'
    class_names = " ".join(classes)
    return f'<span class="{class_names}">{marker}{esc(text)}</span>'


def status_pill(text: str, tone: str = "", *, live: bool = False,
                dot: bool = True) -> str:
    """A status chip with a leading state dot, for live/degraded/offline badges."""
    return pill(text, tone, dot=dot, live=live)


def stat(label: str, value_html: str, sub: str = "", *, size: str = "") -> str:
    """A labelled figure. ``value_html`` is trusted caller-produced markup."""
    classes = "ui-value" + (f" ui-value--{size}" if size else "")
    tail = f'<div class="ui-sub">{esc(sub)}</div>' if sub else ""
    return (
        f'<div><div class="ui-label">{esc(label)}</div>'
        f'<div class="{classes}">{value_html}</div>{tail}</div>'
    )


def money_stat(label: str, value: float | None, sub: str = "", *, currency: str = "$",
               size: str = "") -> str:
    """Monetary stat signed and coloured by sign."""
    return stat(label, f'<span class="{tone_class(value)}">'
                       f"{money(value, currency)}</span>", sub, size=size)


def percent_stat(label: str, value: float | None, sub: str = "", *, size: str = "") -> str:
    """Percentage stat, signed and coloured by sign."""
    return stat(label, f'<span class="{tone_class(value)}">'
                       f"{percent(value)}</span>", sub, size=size)


def kv(label: str, value_html: str) -> str:
    """One key/value line inside a card."""
    return f'<div class="ui-kv"><span class="ui-kv-k">{esc(label)}</span>' \
           f'<span class="ui-kv-v">{value_html}</span></div>'


def rows(pairs: Sequence[tuple[str, str]]) -> str:
    """Stack of key/value lines separated by hairlines."""
    return "".join(kv(label, value) for label, value in pairs)


def empty_state(title: str, body: str = "", *, icon: str = "") -> str:
    """Explicit empty state. Every list in this app uses one instead of blank."""
    mark = (f'<div style="font-size:1.5rem;margin-bottom:.35rem;opacity:.65">{icon}</div>'
            if icon else "")
    tail = f'<div class="ui-empty-body">{esc(body)}</div>' if body else ""
    return (f'<div class="ui-empty">{mark}<div class="ui-empty-title">{esc(title)}</div>'
            f"{tail}</div>")


def skeleton(height: int = 96) -> str:
    """Placeholder block shown while a panel's data is being fetched."""
    return f'<div class="ui-skeleton" style="height:{int(height)}px"></div>'


def error_state(title: str, body: str = "") -> str:
    """Failure state that names what broke and what the user can do next."""
    return empty_state(title, body, icon="⚠")


def info_state(title: str, body: str = "") -> str:
    """Neutral informational state."""
    return empty_state(title, body, icon="ⓘ")


# --------------------------------------------------------------------------
# Visuals rendered without a charting runtime
# --------------------------------------------------------------------------

def sparkline(values: Iterable[float], *, width: int = 96, height: int = 30,
              tone: str = "flat") -> str:
    """Inline SVG trend line for a compact row.

    A watchlist cell is a few hundred bytes of markup; shipping a chart runtime
    to draw twenty 30px lines would cost far more than it returns.
    """
    points = [float(value) for value in values
              if value is not None and math.isfinite(float(value))]
    if len(points) < 2:
        return (f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
                f'aria-hidden="true"></svg>')
    low, high = min(points), max(points)
    span = (high - low) or 1.0
    step = width / (len(points) - 1)
    # Inset by two pixels so the stroke is not clipped at the edges.
    coordinates = " ".join(
        f"{index * step:.2f},{height - 2 - ((value - low) / span) * (height - 4):.2f}"
        for index, value in enumerate(points)
    )
    stroke = {"pos": "var(--ui-profit)", "neg": "var(--ui-loss)"}.get(
        tone, "var(--ui-text-faint)")
    return (f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
            f'preserveAspectRatio="none" aria-hidden="true" style="display:block">'
            f'<polyline points="{coordinates}" fill="none" stroke="{stroke}" '
            f'stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round"/></svg>')


def area_chart(points: Sequence[tuple[str, float]], *, height: int = 180,
               tone: str = "accent", labels: bool = True) -> str:
    """Responsive SVG line chart with a soft fill, for equity-style series.

    Deliberately axis-light: it answers "which way, and by how much", and the
    exact figures are always printed beside the chart.
    """
    usable = [(label, float(value)) for label, value in points
              if value is not None and math.isfinite(float(value))]
    if len(usable) < 2:
        return empty_state("Not enough data to chart yet",
                           "A series needs at least two real observations.")
    width = 720
    values = [value for _, value in usable]
    low, high = min(values), max(values)
    span = (high - low) or 1.0
    low, high = low - span * 0.12, high + span * 0.12
    span = high - low
    top, bottom = 12.0, height - 22.0
    step = width / (len(usable) - 1)
    coordinates = [
        (index * step, bottom - ((value - low) / span) * (bottom - top))
        for index, (_, value) in enumerate(usable)
    ]
    line = " ".join(f"{x:.2f},{y:.2f}" for x, y in coordinates)
    area = f"0,{bottom:.2f} " + line + f" {width},{bottom:.2f}"
    colors = {"pos": "var(--ui-profit)", "neg": "var(--ui-loss)",
              "accent": "var(--ui-accent)", "flat": "var(--ui-text-faint)"}
    stroke = colors.get(tone, colors["accent"])
    annotations = ""
    if labels:
        annotations = (
            f'<text x="0" y="{height - 5}" font-size="10" '
            f'fill="var(--ui-text-faint)">{esc(usable[0][0])}</text>'
            f'<text x="{width}" y="{height - 5}" font-size="10" text-anchor="end" '
            f'fill="var(--ui-text-faint)">{esc(usable[-1][0])}</text>'
        )
    return (
        f'<svg viewBox="0 0 {width} {height}" preserveAspectRatio="none" role="img" '
        f'style="width:100%;height:{height}px;display:block" aria-label="Trend chart">'
        f'<polygon points="{area}" fill="{stroke}" opacity="0.10"/>'
        f'<polyline points="{line}" fill="none" stroke="{stroke}" stroke-width="2" '
        f'stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"/>'
        f"{annotations}</svg>"
    )


def bar_chart(items: Sequence[tuple[str, float]], *, height: int = 160) -> str:
    """Signed bars for daily P&L and monthly returns.

    Each bar is coloured by its own sign so gains and losses are separable
    without reading the axis.
    """
    usable = [(label, float(value)) for label, value in items
              if value is not None and math.isfinite(float(value))]
    if not usable:
        return empty_state("No data to chart yet")
    width = 720
    extent = max(abs(value) for _, value in usable) or 1.0
    gap = 6.0
    bar_width = max(3.0, (width - gap * (len(usable) + 1)) / len(usable))
    plot = height - 20.0
    baseline = plot / 2
    bars = []
    for index, (_, value) in enumerate(usable):
        x = gap + index * (bar_width + gap)
        height_px = max(1.5, (abs(value) / extent) * (baseline - 4))
        y = baseline - height_px if value >= 0 else baseline
        fill = "var(--ui-profit)" if value >= 0 else "var(--ui-loss)"
        bars.append(f'<rect x="{x:.2f}" y="{y:.2f}" width="{bar_width:.2f}" '
                    f'height="{height_px:.2f}" rx="2" fill="{fill}" opacity="0.85"/>')
    axis = (f'<line x1="0" y1="{baseline:.2f}" x2="{width}" y2="{baseline:.2f}" '
            f'stroke="var(--ui-border)" stroke-width="1"/>')
    return (
        f'<svg viewBox="0 0 {width} {height}" preserveAspectRatio="none" role="img" '
        f'style="width:100%;height:{height}px;display:block" aria-label="Bar chart">'
        f'{axis}{"".join(bars)}</svg>'
    )


def table(headers: Sequence[str], body_rows: Sequence[Sequence[str]],
          *, align_right: Sequence[int] = ()) -> str:
    """A responsive, horizontally scrollable table.

    Mobile has no room for a wide grid, so the table scrolls inside its own
    container instead of forcing the whole page sideways.
    """
    if not body_rows:
        return empty_state("Nothing to show yet")
    right = {int(index) for index in align_right}
    head = "".join(
        f'<th style="{"text-align:right" if index in right else ""}">{esc(label)}</th>'
        for index, label in enumerate(headers)
    )
    cells = "".join(
        "<tr>" + "".join(
            f'<td style="{"text-align:right" if index in right else ""}">{cell}</td>'
            for index, cell in enumerate(row)
        ) + "</tr>"
        for row in body_rows
    )
    return (f'<div class="ui-scroll-x"><table class="ui-table"><thead><tr>{head}</tr>'
            f'</thead><tbody>{cells}</tbody></table></div>')


# --------------------------------------------------------------------------
# Domain composites
# --------------------------------------------------------------------------

def greeting(name: str, *, now: datetime | None = None) -> str:
    """Time-aware greeting header.

    The name comes from configuration, never from a login, because this build
    has no authentication and must not imply an account it cannot verify.
    """
    moment = now or datetime.now(timezone.utc)
    hour = moment.hour
    period = ("Good morning" if hour < 12 else
              "Good afternoon" if hour < 18 else "Good evening")
    return (f'<div class="ui-label" style="margin-bottom:.15rem">{period}</div>'
            f'<h1 style="margin:0;font-size:1.6rem">{esc(name)}</h1>')


def strategy_card(name: str, category: str, status: str, status_tone: str,
                  pnl: float | None, *, trades: int = 0, active_positions: int = 0,
                  win_rate: float | None = None, risk: str = "", currency: str = "$",
                  live: bool = False) -> str:
    """One strategy in the platform catalogue.

    Every field is computed from real paper state. When a strategy has never
    run, its P&L renders as an em dash rather than ``$0.00``, so "no data" is
    never mistaken for "flat performance".
    """
    has_history = pnl is not None or trades
    pnl_value = (f'<span class="{tone_class(pnl)}">{money(pnl, currency)}</span>'
                 if has_history else '<span class="ui-flat">--</span>')
    win_value = (percent(win_rate, signed=False) if win_rate is not None
                 else '<span class="ui-flat">--</span>')
    pnl_stat = stat("Today's P&L", pnl_value)
    body = (
        f'<div class="ui-row">'
        f'<div><div style="font-weight:660;font-size:.98rem">{esc(name)}</div>'
        f'<div class="ui-sub">{esc(category)}</div></div>'
        f'{status_pill(status, status_tone, live=live)}</div>'
        f'<div style="margin-top:.7rem">{pnl_stat}</div>'
        f'<div style="margin-top:.55rem">{rows([("Active trades", esc(active_positions)), ("Win rate", win_value), ("Risk status", esc(risk))])}</div>'
    )
    return card(body, variant=status_tone if status_tone in {"profit", "loss"} else "",
                interactive=True)


def quote_row(symbol: str, price: float | None, change_pct: float | None = None,
              change_abs: float | None = None, *, history: Sequence[float] = (),
              currency: str = "$", decimals: int = 4, status: str = "",
              status_tone: str = "") -> str:
    """One watchlist instrument with a sparkline.

    When data is unavailable the row states that plainly instead of showing a
    stale or invented price.
    """
    if price is None:
        return card(
            f'<div class="ui-row"><div style="font-weight:620">{esc(symbol)}</div>'
            f'{status_pill(status or "Unavailable", status_tone or "loss", dot=True)}</div>'
            f'<div class="ui-sub" style="margin-top:.25rem">Market data unavailable</div>',
            variant="loss",
        )
    direction = "pos" if (change_pct or 0) > 0 else "neg" if (change_pct or 0) < 0 else "flat"
    delta = ""
    if change_pct is not None:
        absolute = (f"{change_abs:+,.{decimals}f}" if change_abs is not None else "")
        delta = f'<span class="ui-sub {direction}">{percent(change_pct)} {absolute}</span>'
    body = (
        f'<div class="ui-row"><div>'
        f'<div style="font-weight:640;font-size:.92rem">{esc(symbol)}</div>'
        f'{delta}</div>{sparkline(history, tone=direction, width=88, height=30)}</div>'
        f'<div class="ui-value ui-value--sm" style="margin-top:.45rem">'
        f"{currency}{price:,.{decimals}f}</div>"
    )
    return card(body, interactive=True)


def position_card(symbol: str, side: str, *, entry: float | None = None,
                  price: float | None = None, sl: float | None = None,
                  tp: float | None = None, quantity: float | None = None,
                  pnl: float | None = None, r_multiple: float | None = None,
                  strategy: str = "", opened_at: str = "", currency: str = "$",
                  decimals: int = 2, timeframe: str = "") -> str:
    """A compact open-position card with live P&L and the R multiple."""
    long_side = str(side).upper() == "LONG"
    tone = "profit" if long_side else "loss"
    pnl_html = (f'<span class="{tone_class(pnl)}">{money(pnl, currency)}</span>'
                if pnl is not None else '<span class="ui-flat">--</span>')
    r_html = (f'<span class="{tone_class(r_multiple)}">{ratio(r_multiple, suffix="R")}</span>'
              if r_multiple is not None else '<span class="ui-flat">--</span>')
    when = f" · {esc(timeframe)}" if timeframe else ""
    header = (
        f'<div class="ui-row"><div>'
        f'<div style="font-weight:660">{esc(symbol)}</div>'
        f'<div class="ui-sub">{esc(strategy)}{when}</div>'
        f'</div><div style="text-align:right">{pill(side, tone)}'
        f'<div style="margin-top:.3rem">{pnl_html}</div></div></div>'
    )
    pairs = [
        ("Entry", f"{currency}{entry:,.{decimals}f}" if entry is not None else "--"),
        ("Current", f"{currency}{price:,.{decimals}f}" if price is not None else "--"),
        ("Stop loss", (f'<span class="ui-neg">{currency}{sl:,.{decimals}f}</span>'
                       if sl is not None else "--")),
        ("Take profit", (f'<span class="ui-pos">{currency}{tp:,.{decimals}f}</span>'
                         if tp is not None else "--")),
        ("Quantity", esc(f"{quantity:.8g}") if quantity is not None else "--"),
        ("R multiple", r_html),
    ]
    tail = (f'<div class="ui-sub" style="margin-top:.5rem">Opened {esc(opened_at)}</div>'
            if opened_at else "")
    return card(header + f'<div style="margin-top:.6rem">{rows(pairs)}</div>' + tail,
                variant=tone)


def drawdown_chart(points: Sequence[tuple[str, float]], *, height: int = 140) -> str:
    """Drawdown curve; always loss-coloured because a drawdown is always a loss."""
    return area_chart(points, height=height, tone="neg")