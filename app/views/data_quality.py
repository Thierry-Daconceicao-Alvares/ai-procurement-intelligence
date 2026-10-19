"""Data quality and method: from 3.3 million published rows to a reliable
spend cube, and how each indicator is computed."""

from __future__ import annotations

import pandas as pd
import streamlit as st
import ui

INDICATORS = [
    (
        "Amount increased by modifications",
        "Increase above 10% (supplies, services) or 15% (works); high from 50%",
        "Code de la commande publique, R. 2194-3, R. 2194-5, R. 2194-8",
    ),
    (
        "Amount far from comparable contracts",
        "Model residual of 3.5 to 5 robust standard deviations",
        "Amount model",
    ),
    (
        "Single offer in a formal tender",
        "One offer, formal procedure, amount above the EU threshold",
        "EU Single Market Scoreboard",
    ),
    (
        "High single-offer rate for a buyer",
        "Binomial z-score of 3 or more, and 15 points above the rate expected from its categories",
        "EU Single Market Scoreboard",
    ),
    (
        "Award without competition above the threshold",
        "Procedure without publication or competition, amount above the R. 2122-8 threshold "
        "in force",
        "R. 2122-1 to R. 2122-11",
    ),
    (
        "Possible splitting",
        "Same buyer, supplier and CPV group, same year, different dates: each contract below "
        "the EU threshold, together above it",
        "R. 2121-1 and following",
    ),
    (
        "Dependency on one supplier",
        "One supplier holds at least 60% of a buyer's spend in a division (€1M or more)",
        "Herfindahl-Hirschman index",
    ),
    (
        "Supplier activity unusual for the category",
        "The category accounts for less than 0.2% of the contracts of the supplier's sector",
        "Pairs of NAF and CPV divisions",
    ),
    (
        "Supplier ceased before the award",
        "Ceased in Sirene more than 90 days before the notification",
        "Base Sirene",
    ),
    (
        "Newly created supplier",
        "Company created less than 6 months before an award of €500k or more",
        "Base Sirene",
    ),
    (
        "CPV code probably wrong",
        "Declared division at most 2% likely, another one at least 90%",
        "Text classifier",
    ),
    (
        "Implausible reported amount",
        "One of the amount checks above",
        "Amount checks and model",
    ),
    (
        "Contract declared several times",
        "Same buyer, amount, date, lot and holder, nearly the same description",
        "Deduplication rule",
    ),
    (
        "Framework agreement published per supplier",
        "Same as above, different holders, all framework agreements",
        "Deduplication rule",
    ),
    (
        "Joint purchase published by each member",
        "Different buyers with the same amount, holder and date (or, from €10M, dates "
        "within 60 days of each other)",
        "Deduplication rule",
    ),
]

ISSUES = {
    "placeholder": "Placeholder value, such as 999,999,999 or 99,999,999.99",
    "out_of_bounds": "Below €100, or €10bn and more",
    "default_ceiling": "Same large amount reused by a buyer for unrelated purchases",
    "far_from_expected": "Far from the amount expected for comparable contracts (model)",
}


def render() -> None:
    st.title("Data quality and method", anchor=False)
    funnel = ui.metrics("data_quality")
    amount = ui.metrics("amount_model")
    st.markdown(
        "Published procurement data is the raw material of every indicator in this "
        "application, and it is not clean. Before any analysis, the records that describe the "
        "same award are merged and implausible amounts are set aside."
    )
    if funnel:
        steps = pd.DataFrame(
            [
                ("Rows published (2019-2026 snapshot)", funnel["raw_rows"]),
                ("Contracts notified January 2019 - September 2026", funnel["contracts_in_window"]),
                (
                    "Records of a contract already counted (duplicates)",
                    -funnel["duplicate_records_removed"],
                ),
                (
                    "Framework agreements published once per supplier (merged)",
                    -funnel["framework_records_merged"],
                ),
                (
                    "Joint purchases published by each member of the group (merged)",
                    -funnel.get("joint_purchase_records_merged", 0),
                ),
                ("Contracts analysed", funnel["contracts_kept"]),
                (
                    "  of which with an implausible amount (excluded from totals)",
                    funnel["amounts_implausible"],
                ),
                ("  of which without amount", funnel["amounts_missing"]),
            ],
            columns=["Step", "Records"],
        )
        st.dataframe(
            steps,
            hide_index=True,
            width="stretch",
            column_config={"Records": st.column_config.NumberColumn(format="%,d")},
        )
        ui.tiles(
            [
                (
                    "Sum of all reported amounts",
                    ui.money(funnel["reported_value_all_records_eur"]),
                    "Every record of the window, before merging records and setting aside "
                    "implausible amounts.",
                ),
                (
                    "Awarded value after cleaning",
                    ui.money(funnel["awarded_value_valid_eur"]),
                    "Valid amounts of the contracts analysed, framework maximums included.",
                ),
                (
                    "Excluding framework agreements",
                    ui.money(funnel["awarded_value_excluding_frameworks_eur"]),
                    None,
                ),
            ]
        )

    st.subheader("How implausible amounts are found", anchor=False)
    st.markdown(
        "Three rules catch the errors that need no model: placeholder values entered where an "
        "amount was required (999,999,999, or 399,999,996 for four lots of 99,999,999), "
        "amounts below €100 or of €10bn and more, and default ceilings, when a buyer reports "
        "the same amount of €10M or more for unrelated purchases, a hundred times above its "
        "large contracts.\n\n"
        "Then a gradient-boosting model (LightGBM) predicts the usual amount of each contract "
        "from its category, type, procedure, duration, number of offers, buyer and region. It "
        "is trained with an absolute-error loss, so it estimates a median and is barely moved "
        "by the errors it must find, and it is cross-fitted: each contract is scored by a "
        "model that never saw it. The gap between the reported and the expected amount is "
        "measured in robust standard deviations, estimated per buyer: central purchasing "
        "bodies award contracts of very different sizes. From 5, the amount is implausible "
        "and excluded from totals; from 3.5, it is an atypical amount to review."
    )
    if funnel.get("amounts_implausible_by_issue"):
        reasons = pd.DataFrame(
            [
                (ISSUES.get(issue, issue), n)
                for issue, n in funnel["amounts_implausible_by_issue"].items()
            ],
            columns=["Reason", "Contracts"],
        )
        st.dataframe(
            reasons,
            hide_index=True,
            width="stretch",
            column_config={"Contracts": st.column_config.NumberColumn(format="%,d")},
        )
    if amount:
        ui.tiles(
            [
                (
                    "Contracts scored",
                    ui.count(amount["contracts_scored"]),
                    f"{amount['contracts_scored']:,} contracts with an amount between €100 and "
                    "€10bn.",
                ),
                (
                    "Median error",
                    f"x{10 ** amount['median_abs_error_log10']:.1f}",
                    "Half of the predictions are within this factor of the reported amount.",
                ),
                (
                    "R² (log scale)",
                    f"{amount['r2_log10']:.2f}",
                    "Share of the variance of log amounts explained by the model.",
                ),
            ]
        )

    st.subheader("Indicators", anchor=False)
    st.dataframe(
        pd.DataFrame(INDICATORS, columns=["Alert", "Rule", "Reference"]),
        hide_index=True,
        width="stretch",
        row_height=56,
        column_config={
            "Alert": st.column_config.TextColumn(width="medium"),
            "Rule": st.column_config.TextColumn(width="large"),
        },
    )
    st.subheader("Limits", anchor=False)
    st.markdown(
        "- Amounts are awarded amounts, not payments; framework agreements report a maximum.\n"
        "- Thresholds apply from the launch of a consultation, which the data does not give: "
        "the notification date is used instead.\n"
        "- Buyers do not all publish their data, and recent months are incomplete.\n"
        "- An alert is a risk indicator, never a finding: most alerts have a legitimate "
        "explanation that only the buyer's file can show."
    )
    st.caption(ui.SOURCE_NOTE)


render()
