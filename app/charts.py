"""Plotly charts with one visual system.

Colours follow the validated reference palette of the project (categorical
slots in a fixed order, reserved status colours); marks are thin with 4px
rounded ends, gridlines are solid hairlines and every chart has a hover
tooltip. Titles are written by the page above each chart, so that they never
collide with a legend; a chart with one series has no legend.
"""

from __future__ import annotations

import math

import pandas as pd
import plotly.graph_objects as go

from procurement_intel.formatting import eur

SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
MUTED_SERIES = "#c3c2b7"
INK = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
GRID = "#e1e0d9"
BASELINE = "#c3c2b7"
SURFACE = "#fcfcfb"
STATUS = {"high": "#d03b3b", "medium": "#ec835a", "low": "#fab219", "good": "#0ca30c"}
FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif'
CONFIG = {"displayModeBar": False}


def _layout(fig: go.Figure, height: int, legend: bool = False) -> go.Figure:
    fig.update_layout(
        height=height,
        margin={"l": 8, "r": 16, "t": 32 if legend else 8, "b": 8},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font={"family": FONT, "size": 12, "color": INK_SECONDARY},
        hoverlabel={
            "bgcolor": "#ffffff",
            "bordercolor": GRID,
            "font": {"family": FONT, "color": INK},
        },
        barcornerradius=4,
        bargap=0.45,
        showlegend=legend,
        legend={"orientation": "h", "y": 1.0, "yanchor": "bottom", "x": 0, "title": None},
    )
    fig.update_xaxes(
        showgrid=False, linecolor=BASELINE, tickfont={"color": INK_MUTED}, zeroline=False
    )
    fig.update_yaxes(
        gridcolor=GRID,
        gridwidth=1,
        linecolor=BASELINE,
        zeroline=False,
        tickfont={"color": INK_MUTED},
    )
    return fig


def nice_ticks(maximum: float, count: int = 4) -> list[float]:
    """About `count` round tick values from 0 to just above `maximum`."""
    if not maximum or maximum <= 0 or math.isnan(maximum):
        return [0.0]
    raw = maximum / count
    magnitude = 10 ** math.floor(math.log10(raw))
    step = next(s for s in (1, 2, 2.5, 5, 10) if raw <= s * magnitude) * magnitude
    return [i * step for i in range(math.ceil(maximum / step) + 1)]


def money_axis(fig: go.Figure, maximum: float, axis: str = "y", count: int = 4) -> go.Figure:
    """Euro ticks written like the rest of the application: €0, €100bn, €1.2tn."""
    ticks = nice_ticks(maximum, count)
    update = fig.update_yaxes if axis == "y" else fig.update_xaxes
    update(tickvals=ticks, ticktext=[eur(t) for t in ticks], range=[0, ticks[-1]])
    return fig


def stacked_years(frame: pd.DataFrame) -> go.Figure:
    """Awarded value per year: contracts and framework maximums, stacked."""
    fig = go.Figure()
    for column, name, color in (
        ("contracts_value", "Contracts", SERIES[0]),
        ("framework_value", "Framework agreements (maximum)", SERIES[1]),
    ):
        values = frame[column].fillna(0)
        fig.add_bar(
            x=frame["year"].astype(str),
            y=values,
            name=name,
            marker={"color": color, "line": {"color": SURFACE, "width": 2}},
            customdata=[eur(v) for v in values],
            hovertemplate="%{x} · " + name + ": %{customdata}<extra></extra>",
        )
    fig.update_layout(barmode="stack")
    fig.update_xaxes(type="category")
    total = (frame["contracts_value"].fillna(0) + frame["framework_value"].fillna(0)).max()
    return money_axis(_layout(fig, 300, legend=True), total)


def _shorten(text: str, width: int) -> str:
    text = str(text)
    return text if len(text) <= width else text[: width - 1].rstrip() + "…"


def horizontal_bars(
    frame: pd.DataFrame,
    label: str,
    value: str,
    money: bool = True,
    hover_extra: str | None = None,
    label_width: int = 34,
) -> go.Figure:
    """Ranked bars, largest on top. Long labels are shortened on the axis and
    shown in full in the tooltip."""
    frame = frame.iloc[::-1]
    values = frame[value].fillna(0)
    shown = [_shorten(t, label_width) for t in frame[label]]
    amounts = [eur(v) if money else f"{v:,.0f}" for v in values]
    extra = frame[hover_extra].tolist() if hover_extra else [""] * len(frame)
    custom = list(zip(frame[label].astype(str), amounts, extra, strict=True))
    fig = go.Figure(
        go.Bar(
            x=values,
            y=shown,
            orientation="h",
            marker={"color": SERIES[0]},
            customdata=custom,
            hovertemplate="<b>%{customdata[0]}</b><br>%{customdata[1]}"
            + ("<br>%{customdata[2]}" if hover_extra else "")
            + "<extra></extra>",
        )
    )
    fig = _layout(fig, max(200, 28 * len(frame) + 40))
    fig.update_yaxes(gridcolor="rgba(0,0,0,0)", tickfont={"color": INK_SECONDARY})
    fig.update_xaxes(showgrid=True, gridcolor=GRID)
    if money:
        return money_axis(fig, values.max(), "x", count=4)
    fig.update_xaxes(nticks=4, tickformat=",")
    return fig


def rate_columns(frame: pd.DataFrame, x: str, y: str, count: str | None = None) -> go.Figure:
    """A share per period (single-offer rate per year, for instance), as columns."""
    extra = [f"{n:,} procedures" for n in frame[count]] if count else [""] * len(frame)
    fig = go.Figure(
        go.Bar(
            x=frame[x].astype(str),
            y=frame[y],
            marker={"color": SERIES[0]},
            customdata=extra,
            hovertemplate="%{x}: %{y:.1%}<br>%{customdata}<extra></extra>",
            text=[f"{v:.0%}" for v in frame[y]],
            textposition="outside",
            textfont={"color": INK_SECONDARY},
            cliponaxis=False,
        )
    )
    fig = _layout(fig, 260)
    fig.update_xaxes(type="category")
    top = max(float(frame[y].max()) * 1.25, 0.05)
    fig.update_yaxes(tickformat=".0%", range=[0, top], nticks=4)
    return fig


def kraljic(frame: pd.DataFrame, highlight: str | None = None) -> go.Figure:
    """Categories by value at stake (x) and supply risk (y), split at the medians."""
    fig = go.Figure()
    base = frame if highlight is None else frame[frame["cpv_division"] != highlight]
    selected = frame[frame["cpv_division"] == highlight] if highlight else frame.iloc[0:0]
    for data, color, size, name in (
        (base, SERIES[0] if highlight is None else MUTED_SERIES, 8, "Other categories"),
        (selected, SERIES[0], 10, "Selected division"),
    ):
        if data.empty:
            continue
        custom = list(
            zip(
                data["cpv_group_label"],
                [eur(v) for v in data["total_value"]],
                data["suppliers"],
                data["avg_offers"],
                data["single_bid_share"],
                strict=True,
            )
        )
        fig.add_scatter(
            x=data["value_percentile"],
            y=data["supply_risk"],
            mode="markers",
            name=name,
            marker={"size": size, "color": color, "line": {"color": SURFACE, "width": 2}},
            customdata=custom,
            hovertemplate=(
                "<b>%{customdata[0]}</b><br>Value: %{customdata[1]}<br>"
                "Suppliers: %{customdata[2]:,}<br>Average offers: %{customdata[3]:.1f}<br>"
                "Single offer: %{customdata[4]:.0%}<extra></extra>"
            ),
        )
    for x, y, text in (
        (0.75, 0.97, "Strategic"),
        (0.25, 0.97, "Bottleneck"),
        (0.75, 0.03, "Leverage"),
        (0.25, 0.03, "Non-critical"),
    ):
        fig.add_annotation(
            x=x, y=y, text=text, showarrow=False, font={"color": INK_MUTED, "size": 12}
        )
    fig.add_vline(x=0.5, line={"color": BASELINE, "width": 1})
    fig.add_hline(y=0.5, line={"color": BASELINE, "width": 1})
    fig = _layout(fig, 460, legend=highlight is not None)
    fig.update_xaxes(
        title="Value at stake (percentile among categories)", range=[0, 1], tickformat=".0%"
    )
    fig.update_yaxes(title="Supply risk (percentile)", range=[0, 1], tickformat=".0%")
    return fig
