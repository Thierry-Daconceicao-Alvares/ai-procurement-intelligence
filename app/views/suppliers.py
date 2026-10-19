"""Suppliers: concentration, largest suppliers, segments and risk alerts."""

from __future__ import annotations

import charts
import pandas as pd
import streamlit as st
import ui

from procurement_intel import analytics


@st.cache_data(show_spinner="Computing the suppliers...")
def _top(filters: analytics.Filters) -> pd.DataFrame:
    return analytics.top_suppliers(ui.connection(), filters, limit=25)


@st.cache_data(show_spinner=False)
def _segments() -> pd.DataFrame:
    return analytics.segments(ui.connection())


@st.cache_data(show_spinner=False)
def _concentration(filters: analytics.Filters) -> dict:
    return analytics.supplier_concentration(ui.connection(), filters)


@st.cache_data(show_spinner=False)
def _supplier_alerts(filters: analytics.Filters) -> pd.DataFrame:
    return analytics.alerts(ui.connection(), filters, categories=("Supplier risk",), limit=200)


def render() -> None:
    filters: analytics.Filters = st.session_state.filters
    st.title("Suppliers and supply risk", anchor=False)
    st.caption(f"{ui.scope_label(filters)} · {ui.period_label(filters)}")

    concentration = _concentration(filters)
    top = _top(filters)
    ui.tiles(
        [
            (
                "Share of the 10 largest suppliers",
                ui.percent(concentration["top10_share"]),
                "Share of the awarded value held by the 10 largest suppliers.",
            ),
            (
                "Supplier concentration (HHI)",
                f"{(concentration['hhi'] or 0) * 10_000:,.0f}",
                "Herfindahl-Hirschman index: sum of the squared market shares of the suppliers, "
                "in percent, from near 0 (many small suppliers) to 10,000 (a single supplier).",
            ),
            (
                "Largest supplier",
                ui.percent(top["share"].iloc[0]) if not top.empty else "n/a",
                top["supplier_name"].iloc[0] if not top.empty else None,
            ),
        ]
    )

    st.subheader("Largest suppliers", anchor=False)
    st.caption(
        "Amounts of contracts with several holders are shared equally between them. Names of "
        "individual entrepreneurs are withheld: they are natural persons."
    )
    table = top[
        [
            "supplier_name",
            "value",
            "share",
            "contracts",
            "buyers",
            "holder_size",
            "segment",
            "naf_label",
        ]
    ].copy()
    table["value"] = table["value"].map(ui.money)
    st.dataframe(
        table.rename(
            columns={
                "supplier_name": "Supplier",
                "value": "Value",
                "share": "Share",
                "contracts": "Contracts",
                "buyers": "Buyers",
                "holder_size": "Size",
                "segment": "Segment",
                "naf_label": "Main activity (NAF, French label)",
            }
        ),
        hide_index=True,
        width="stretch",
        column_config={
            "Share": st.column_config.NumberColumn(format="percent"),
            "Contracts": st.column_config.NumberColumn(format="%,d"),
            "Buyers": st.column_config.NumberColumn(format="%,d"),
        },
    )

    st.subheader("Supplier segments", anchor=False)
    st.markdown(
        "Suppliers with at least three contracts are grouped by k-means on their public "
        "business: value, value per contract, contracts, buyers, categories, regions, "
        "distance to buyers and dependency on their main buyer. Segment names come from the "
        "features that distinguish each group."
    )
    segments = _segments()
    left, right = st.columns([2, 3], gap="large")
    with left:
        st.markdown("**Suppliers per segment**")
        st.plotly_chart(
            charts.horizontal_bars(segments, "segment", "suppliers", money=False),
            width="stretch",
            config=charts.CONFIG,
        )
    with right:
        segments = segments.assign(median_value=segments["median_value"].map(ui.money))
        st.dataframe(
            segments.rename(
                columns={
                    "segment": "Segment",
                    "suppliers": "Suppliers",
                    "median_contracts": "Median contracts",
                    "median_buyers": "Median buyers",
                    "median_value": "Median value",
                    "median_top_buyer_share": "Median share of main buyer",
                    "median_distance_km": "Median distance (km)",
                }
            ),
            hide_index=True,
            width="stretch",
            column_config={
                "Median share of main buyer": st.column_config.NumberColumn(format="percent"),
                "Median distance (km)": st.column_config.NumberColumn(format="%.0f"),
            },
        )

    st.subheader("Supplier risk alerts", anchor=False)
    alerts = _supplier_alerts(filters)
    if alerts.empty:
        st.info("No supplier risk alert for this selection.")
    else:
        table = alerts[["label", "title", "buyer_name", "supplier_name", "amount_at_stake"]].copy()
        table["amount_at_stake"] = table["amount_at_stake"].map(ui.money)
        st.dataframe(
            table.rename(
                columns={
                    "label": "Alert",
                    "title": "What was found",
                    "buyer_name": "Buyer",
                    "supplier_name": "Supplier",
                    "amount_at_stake": "At stake",
                }
            ),
            hide_index=True,
            width="stretch",
        )
        st.page_link("views/alerts.py", label="Review them in the queue", icon=":material/rule:")


render()
