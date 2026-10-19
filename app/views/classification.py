"""Spend classification: how well a description predicts its CPV division,
a live classifier and the codes that look wrong."""

from __future__ import annotations

import pandas as pd
import streamlit as st
import ui

from procurement_intel import analytics
from procurement_intel.classifier import classify, load_app_model


@st.cache_resource(show_spinner="Loading the classifier...")
def _model():
    return load_app_model(ui.settings().model_dir)


@st.cache_data(show_spinner=False)
def _division_labels() -> dict:
    frame = (
        ui.connection()
        .execute("SELECT cpv_division, cpv_label FROM cpv WHERE cpv_level = 'division'")
        .fetchdf()
    )
    return dict(zip(frame["cpv_division"], frame["cpv_label"], strict=True))


@st.cache_data(show_spinner=False)
def _mismatches(filters: analytics.Filters) -> pd.DataFrame:
    return analytics.alerts(ui.connection(), filters, types=("cpv_mismatch",), limit=300)


def render() -> None:
    filters: analytics.Filters = st.session_state.filters
    labels = _division_labels()
    st.title("Spend classification", anchor=False)
    st.markdown(
        "Buyers choose the CPV code of each contract themselves, and some choose a wrong one: "
        "the spend then lands in the wrong category. A text classifier (TF-IDF of words and "
        "word pairs, linear model trained by stochastic gradient descent) learned the link "
        "between descriptions and the 45 CPV divisions."
    )
    metrics = ui.metrics("cpv_classifier")
    model = metrics.get("model", {})
    baseline = metrics.get("baseline", {})
    if model:
        ui.tiles(
            [
                (
                    "Accuracy",
                    ui.percent(model["accuracy"], 1),
                    f"Naive Bayes baseline: {ui.percent(baseline.get('accuracy'), 1)}",
                ),
                (
                    "Top-3 accuracy",
                    ui.percent(model["top3_accuracy"], 1),
                    "The declared division is among the 3 most likely.",
                ),
                (
                    "Macro F1",
                    f"{model['macro_f1']:.3f}",
                    f"Average over divisions; baseline {baseline.get('macro_f1', 0):.3f}.",
                ),
                (
                    "Test contracts",
                    f"{model['test_contracts']:,}",
                    "Notified in 2025-2026, with descriptions never seen in 2019-2024.",
                ),
            ]
        )
        confidence = pd.DataFrame(model["by_confidence"])
        confidence.columns = ["Minimum probability", "Share of contracts", "Accuracy"]
        st.markdown(
            "The model knows when it is sure: the higher its probability, the more often it "
            "agrees with the declared code. Labels are themselves noisy, so 100% is out of reach."
        )
        st.dataframe(
            confidence,
            hide_index=True,
            column_config={
                "Share of contracts": st.column_config.NumberColumn(format="percent"),
                "Accuracy": st.column_config.NumberColumn(format="percent"),
            },
        )

    st.subheader("Classify a description", anchor=False)
    text = st.text_input(
        "Description of a purchase (in French, like the published data)",
        value="Fourniture de repas en liaison froide pour les restaurants scolaires",
    )
    classifier = _model()
    if classifier is None:
        st.info("The classifier is not available: run the build to train it.")
    elif text.strip():
        results = classify(classifier, text)
        st.dataframe(
            pd.DataFrame(
                [{"Division": d, "Label": labels.get(d, ""), "Probability": p} for d, p in results]
            ),
            hide_index=True,
            column_config={
                "Probability": st.column_config.ProgressColumn(
                    min_value=0, max_value=1, format="%.2f"
                )
            },
        )

    st.subheader("Codes that look wrong", anchor=False)
    st.caption(
        "Contracts whose declared division gets a probability of at most 2% while another "
        "division gets at least 90%. Review them in the queue to confirm or reject."
    )
    mismatches = _mismatches(filters)
    if mismatches.empty:
        st.info("No suspicious code for this selection.")
    else:
        st.dataframe(
            mismatches[["title", "buyer_name", "amount_at_stake"]].rename(
                columns={
                    "title": "What was found",
                    "buyer_name": "Buyer",
                    "amount_at_stake": "Value (€)",
                }
            ),
            hide_index=True,
            width="stretch",
            column_config={"Value (€)": st.column_config.NumberColumn(format="%,.0f")},
        )


render()
