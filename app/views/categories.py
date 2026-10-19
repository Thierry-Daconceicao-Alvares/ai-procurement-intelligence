"""Categories: spend by CPV division and the Kraljic matrix of markets."""

from __future__ import annotations

import charts
import pandas as pd
import streamlit as st
import ui

from procurement_intel import analytics


@st.cache_data(show_spinner=False)
def _markets() -> pd.DataFrame:
    return analytics.category_markets(ui.connection())


@st.cache_data(show_spinner="Computing the categories...")
def _divisions(filters: analytics.Filters) -> pd.DataFrame:
    return analytics.overview(ui.connection(), filters).by_division


def render() -> None:
    filters: analytics.Filters = st.session_state.filters
    st.title("Categories and markets", anchor=False)
    st.caption(f"{ui.scope_label(filters)} · {ui.period_label(filters)}")

    divisions = _divisions(filters)
    left, right = st.columns([3, 2], gap="large")
    with left:
        st.markdown("**Awarded value by CPV division**")
        top = divisions.head(15).copy()
        top["label"] = top["cpv_division"] + " · " + top["label"]
        top["hover"] = top.apply(lambda r: f"{r.contracts:,} contracts · {r.share:.0%}", axis=1)
        st.plotly_chart(
            charts.horizontal_bars(top, "label", "value", hover_extra="hover", label_width=40),
            width="stretch",
            config=charts.CONFIG,
        )
    with right:
        st.markdown("**How to read the categories**")
        st.markdown(
            "CPV codes (Common Procurement Vocabulary) classify what public buyers purchase. "
            "Divisions are the first two digits, groups the first three. Buyers choose the "
            "code themselves: the Spend classification page shows the codes that look wrong."
        )
        table = divisions.head(15)[["cpv_division", "label", "value", "share"]].copy()
        table["value"] = table["value"].map(ui.money)
        st.dataframe(
            table.rename(
                columns={
                    "cpv_division": "Division",
                    "label": "Label",
                    "value": "Value",
                    "share": "Share",
                }
            ),
            hide_index=True,
            width="stretch",
            column_config={"Share": st.column_config.NumberColumn(format="percent")},
        )

    st.subheader("Kraljic matrix of public markets", anchor=False)
    st.markdown(
        "Each point is a CPV group with at least 30 competitive procedures reporting their "
        "offers. **Value at stake** is the awarded value of the group, as a percentile among "
        "groups. **Supply risk** combines the concentration of suppliers (HHI), the share of "
        "procedures with a single offer and the scarcity of offers. Quadrants split at the "
        "medians: strategic (high value, high risk), bottleneck, leverage and non-critical."
    )
    markets = _markets()
    options = ["All divisions", *sorted(markets["cpv_division"].unique())]
    labels = dict(zip(divisions["cpv_division"], divisions["label"], strict=False))
    chosen = st.selectbox(
        "Highlight a division",
        options,
        format_func=lambda d: d if d == "All divisions" else f"{d} · {labels.get(d, '')}",
    )
    highlight = None if chosen == "All divisions" else chosen
    st.plotly_chart(charts.kraljic(markets, highlight), width="stretch", config=charts.CONFIG)
    shown = markets if highlight is None else markets[markets["cpv_division"] == highlight]
    shown = shown.assign(hhi=shown["hhi"] * 10_000)
    st.dataframe(
        shown[
            [
                "cpv_group",
                "cpv_group_label",
                "kraljic_quadrant",
                "total_value",
                "suppliers",
                "hhi",
                "avg_offers",
                "single_bid_share",
                "supply_risk",
            ]
        ]
        .sort_values("total_value", ascending=False)
        .rename(
            columns={
                "cpv_group": "Group",
                "cpv_group_label": "Label",
                "kraljic_quadrant": "Quadrant",
                "total_value": "Value (€)",
                "suppliers": "Suppliers",
                "hhi": "HHI",
                "avg_offers": "Average offers",
                "single_bid_share": "Single offer",
                "supply_risk": "Supply risk",
            }
        ),
        hide_index=True,
        width="stretch",
        column_config={
            "Value (€)": st.column_config.NumberColumn(format="%,.0f"),
            "HHI": st.column_config.NumberColumn(
                format="%,.0f", help="Herfindahl-Hirschman index, from 0 to 10,000"
            ),
            "Average offers": st.column_config.NumberColumn(format="%.1f"),
            "Single offer": st.column_config.NumberColumn(format="percent"),
            "Supply risk": st.column_config.ProgressColumn(min_value=0, max_value=1, format="%.2f"),
        },
    )
    st.caption(
        "Markets are national: the matrix shows the structure of public demand and supply in "
        "France, not the position of one buyer."
    )


render()
