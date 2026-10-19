"""Overview: where the money goes, how much competition, what to review."""

from __future__ import annotations

import charts
import streamlit as st
import ui
from assistant import briefing_panel

from procurement_intel import analytics

# Below this share of competitive procedures reporting their number of offers,
# a yearly single-offer rate says more about reporting than about competition.
MIN_REPORTING_SHARE = 0.5


@st.cache_data(show_spinner="Computing the overview...")
def _overview(filters: analytics.Filters) -> analytics.Overview:
    con = ui.connection()
    result = analytics.overview(con, filters)
    result.extras["concentration"] = analytics.supplier_concentration(con, filters)
    result.extras["alerts"] = analytics.alert_counts(con, filters)
    return result


def render() -> None:
    filters: analytics.Filters = st.session_state.filters
    data = _overview(filters)
    k = data.kpis
    concentration = data.extras["concentration"]
    st.title("Spend overview", anchor=False)
    st.caption(f"{ui.scope_label(filters)} · contracts notified {ui.period_label(filters)}")

    ui.tiles(
        [
            (
                "Awarded value",
                ui.money(k["awarded_value"]),
                "Sum of valid awarded amounts, excluding VAT. Framework agreements count for "
                "their maximum.",
            ),
            (
                "Contracts",
                ui.count(k["contracts"]),
                f"{int(k['contracts']):,} contracts, after merging the records of one award.",
            ),
            ("Buyers", ui.count(k["buyers"]), f"{int(k['buyers']):,} public buyers."),
            ("Suppliers", ui.count(k["suppliers"]), f"{int(k['suppliers'] or 0):,} suppliers."),
        ]
    )
    ui.tiles(
        [
            (
                "Single-offer rate",
                ui.percent(k["single_bid_rate"]),
                "Share of competitive procedures that received one offer, where the number of "
                "offers is reported.",
            ),
            (
                "Awarded without competition",
                ui.percent(k["non_competitive_share"]),
                "Share of contracts awarded without publication or competition.",
            ),
            (
                "SME share of value",
                ui.percent(k["sme_share"]),
                "Share of the awarded value held by small and medium-sized companies.",
            ),
            (
                "10 largest suppliers",
                ui.percent(concentration["top10_share"]),
                "Share of the awarded value held by the 10 largest suppliers.",
            ),
        ]
    )

    left, right = st.columns(2, gap="large")
    with left:
        st.markdown("**Awarded value by year**")
        st.plotly_chart(charts.stacked_years(data.by_year), width="stretch", config=charts.CONFIG)
        st.caption(
            "Framework agreements report the maximum they allow, which is rarely spent in full."
        )
    with right:
        st.markdown("**Largest CPV divisions by value**")
        top = data.by_division.head(10).copy()
        top["hover"] = top.apply(lambda r: f"{r.contracts:,} contracts · {r.share:.0%}", axis=1)
        st.plotly_chart(
            charts.horizontal_bars(top, "label", "value", hover_extra="hover"),
            width="stretch",
            config=charts.CONFIG,
        )

    left, right = st.columns(2, gap="large")
    with left:
        st.markdown("**Competitive procedures with a single offer**")
        years = data.single_bid_by_year
        reported = years[
            (years["reporting_share"] >= MIN_REPORTING_SHARE) & (years["procedures"] >= 30)
        ]
        if reported.empty:
            st.info("Too few procedures report their number of offers in this selection.")
        else:
            st.plotly_chart(
                charts.rate_columns(reported, "year", "single_bid_rate", "procedures"),
                width="stretch",
                config=charts.CONFIG,
            )
            hidden = years[~years["year"].isin(reported["year"])]
            if not hidden.empty:
                st.caption(
                    "Years in which fewer than half of the competitive procedures report their "
                    f"number of offers are not shown ({', '.join(map(str, hidden['year']))})."
                )
    with right:
        st.markdown("**Alerts to review**")
        alerts = data.extras["alerts"]
        if alerts.empty:
            st.info("No alert for this selection.")
        else:
            by_category = (
                alerts.groupby("category", as_index=False)[["count", "amount_at_stake"]]
                .sum()
                .sort_values("count", ascending=False)
            )
            by_category["amount_at_stake"] = by_category["amount_at_stake"].map(ui.money)
            st.dataframe(
                by_category.rename(
                    columns={
                        "category": "Category",
                        "count": "Alerts",
                        "amount_at_stake": "Amount at stake",
                    }
                ),
                hide_index=True,
                width="stretch",
                column_config={"Alerts": st.column_config.NumberColumn(format="%,d")},
            )
            st.caption(
                "The amount at stake is the amount an alert is about (the awarded amount, the "
                "increase, the reporting error...), not a loss."
            )
            st.page_link("views/alerts.py", label="Open the review queue", icon=":material/rule:")

    briefing_panel(filters, data)


render()
