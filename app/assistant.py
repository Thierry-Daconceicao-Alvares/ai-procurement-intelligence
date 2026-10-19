"""The AI assistant panels: a briefing of the current view and the explanation
of an alert. Both show where the text comes from (model or template) and
whether every figure was found in the data."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st
import ui

from procurement_intel import analytics
from procurement_intel.explain import (
    AlertExplanation,
    Briefing,
    Result,
    alert_context,
    brief_view,
    explain_alert,
    template_briefing,
    template_explanation,
)
from procurement_intel.llm import Gemini, LLMError, LLMQuotaExhausted


@st.cache_resource(show_spinner=False)
def gemini() -> Gemini | None:
    settings = ui.settings()
    if not settings.gemini_api_key:
        return None
    return Gemini(settings.gemini_api_key, settings.generation_model)


@st.cache_data(show_spinner=False)
def precomputed() -> dict:
    """Explanations and briefings generated in advance by the "Generate AI
    texts" workflow, keyed by alert id or view."""
    path = Path(ui.settings().marts_dir) / "ai_texts.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def provenance(result: Result) -> None:
    if result.source == "template":
        st.caption(":material/description: Written from templates: no language model configured.")
        return
    check = (
        ":material/verified: every figure was found in the data"
        if result.verified
        else f":material/warning: figures not found in the data: {', '.join(result.unverified)}"
    )
    st.caption(f"Written by {result.source} on {result.created_at[:10]} · {check}")


def _result_from_record(record: dict, schema: type) -> Result:
    return Result(
        content=schema.model_validate(record["content"]),
        source=record["source"],
        prompt_version=record["prompt_version"],
        created_at=record["created_at"],
        unverified=record.get("unverified") or [],
    )


def view_facts(filters: analytics.Filters, data: analytics.Overview) -> dict:
    k = data.kpis
    top = data.by_division.head(5)
    alerts = data.extras.get("alerts", pd.DataFrame())
    return {
        "scope": ui.scope_label(filters),
        "period": ui.period_label(filters),
        "awarded_value": k["awarded_value"],
        "contracts": int(k["contracts"]),
        "buyers": int(k["buyers"]),
        "suppliers": int(k["suppliers"] or 0),
        "framework_share_of_value": (k["framework_value"] or 0) / k["awarded_value"]
        if k["awarded_value"]
        else None,
        "top_categories": [
            {"label": row.label, "value": row.value, "share": row.share} for row in top.itertuples()
        ],
        "top10_supplier_share": data.extras["concentration"]["top10_share"],
        "sme_share": k["sme_share"],
        "single_bid_rate": k["single_bid_rate"],
        "non_competitive_share": k["non_competitive_share"],
        "alerts": [
            {"label": row.label, "count": int(row.count), "amount_at_stake": row.amount_at_stake}
            for row in alerts.head(6).itertuples()
        ],
        "value_by_year": {
            str(row.year): (row.contracts_value or 0) + (row.framework_value or 0)
            for row in data.by_year.itertuples()
        },
    }


def show_briefing(result: Result) -> None:
    briefing: Briefing = result.content  # type: ignore[assignment]
    st.markdown(f"**{ui.md_escape(briefing.headline)}**")
    columns = st.columns(2)
    for index, section in enumerate(briefing.sections):
        with columns[index % 2]:
            st.markdown(f"**{ui.md_escape(section.title)}**")
            st.markdown("\n".join(f"- {ui.md_escape(b)}" for b in section.bullets))
    provenance(result)


def briefing_panel(filters: analytics.Filters, data: analytics.Overview) -> None:
    st.subheader("AI briefing", anchor=False)
    facts = view_facts(filters, data)
    key = json.dumps(facts["scope"] + facts["period"])
    stored = st.session_state.setdefault("briefings", {})
    record = (
        precomputed().get("briefings", {}).get("national")
        if filters.is_national and (filters.first_year, filters.last_year) == (2019, 2026)
        else None
    )
    if key in stored:
        show_briefing(stored[key])
    elif record:
        show_briefing(_result_from_record(record, Briefing))
    elif gemini() is not None:
        if st.button("Write the briefing of this view", icon=":material/auto_awesome:"):
            try:
                with st.spinner("Writing..."):
                    stored[key] = brief_view(gemini(), facts)
                st.rerun()
            except LLMQuotaExhausted as exc:
                st.warning(str(exc))
            except LLMError as exc:
                st.warning(f"The model is not available: {exc}")
        show_briefing(template_briefing(facts))
    else:
        show_briefing(template_briefing(facts))


def explanation_panel(alert: dict, names: dict) -> Result:
    """Explanation of an alert: generated in advance, generated now, or
    written from templates. Returns what the reviewer saw."""
    context = alert_context(alert, names)
    stored = st.session_state.setdefault("explanations", {})
    record = precomputed().get("alerts", {}).get(alert["alert_id"])
    if alert["alert_id"] in stored:
        result = stored[alert["alert_id"]]
    elif record:
        result = _result_from_record(record, AlertExplanation)
    else:
        result = template_explanation(context, alert["alert_type"])
        if gemini() is not None and st.button(
            "Explain with AI", icon=":material/auto_awesome:", key=f"explain-{alert['alert_id']}"
        ):
            try:
                with st.spinner("Writing the explanation..."):
                    stored[alert["alert_id"]] = explain_alert(gemini(), context)
                st.rerun()
            except LLMQuotaExhausted as exc:
                st.warning(str(exc))
            except LLMError as exc:
                st.warning(f"The model is not available: {exc}")
    explanation: AlertExplanation = result.content  # type: ignore[assignment]
    st.markdown(ui.md_escape(explanation.summary))
    st.markdown(f"**Why it matters.** {ui.md_escape(explanation.why_it_matters)}")
    st.markdown("**What to check**")
    st.markdown("\n".join(f"- {ui.md_escape(c)}" for c in explanation.checks))
    st.markdown(
        f"**Possible legitimate explanations.** {ui.md_escape(explanation.legitimate_explanations)}"
    )
    provenance(result)
    return result
