"""Review queue: alerts ordered by priority, their evidence, an explanation
and the reviewer's decision, recorded in the decision log."""

from __future__ import annotations

import pandas as pd
import streamlit as st
import ui
from assistant import explanation_panel

from procurement_intel import analytics
from procurement_intel.alerts import CATEGORIES, TYPES
from procurement_intel.decisions import DECISIONS, Decision, evidence_hash

SEVERITY_BADGE = {"high": ("High", "red"), "medium": ("Medium", "orange"), "low": ("Low", "gray")}
DEFAULT_CATEGORIES = [CATEGORIES["price"], CATEGORIES["leakage"], CATEGORIES["supplier"]]


@st.cache_data(show_spinner="Loading the alerts...")
def _alerts(filters, categories, types, severities) -> pd.DataFrame:
    return analytics.alerts(ui.connection(), filters, categories, types, severities, limit=500)


def _decision_form(alert: dict, result) -> None:
    log = ui.decision_log()
    st.markdown("**Recommendation**")
    recommendation = st.text_area(
        "Recommendation as you accept it",
        value=alert["recommended_action"],
        key=f"recommendation-{alert['alert_id']}",
        label_visibility="collapsed",
        height=90,
    )
    with st.form(f"decision-{alert['alert_id']}", border=True):
        decision = st.radio("Decision", DECISIONS, horizontal=True)
        reason = st.text_input("Reason", placeholder="e.g. urgency documented in the award report")
        reviewer = st.text_input("Reviewer", value=st.session_state.get("reviewer", ""))
        if st.form_submit_button("Record the decision", icon=":material/task_alt:"):
            try:
                log.record(
                    Decision(
                        alert_id=alert["alert_id"],
                        alert_type=alert["alert_type"],
                        decision=decision,
                        reason=reason,
                        recommendation=recommendation,
                        explanation_source=result.source,
                        prompt_version=result.prompt_version,
                        evidence_sha256=evidence_hash(alert["evidence"]),
                        decided_by=reviewer,
                    )
                )
                st.session_state.reviewer = reviewer
                st.success("Decision recorded in the decision log.")
            except ValueError as exc:
                st.error(str(exc))
    history = log.history(alert["alert_id"])
    if not history.empty:
        st.caption("Decisions on this alert")
        st.dataframe(
            history[["decided_at", "decided_by", "decision", "reason", "explanation_source"]],
            hide_index=True,
            width="stretch",
        )


def _detail(alert: dict) -> None:
    con = ui.connection()
    names = analytics.alert_names(con, alert)
    label, color = SEVERITY_BADGE[alert["severity"]]
    st.markdown(f"#### {ui.md_escape(alert['label'])}")
    st.badge(f"{label} severity", color=color)
    st.markdown(f"**{ui.md_escape(alert['title'])}**")
    left, right = st.columns([2, 3], gap="large")
    with left:
        facts = []
        if names.get("buyer_name"):
            facts.append(("Buyer", names["buyer_name"]))
        if names.get("supplier_name"):
            facts.append(("Supplier", names["supplier_name"]))
        if names.get("cpv_label"):
            facts.append(("Category", names["cpv_label"]))
        if names.get("description"):
            facts.append(("Description", names["description"]))
        for name, value in facts:
            st.markdown(f"**{name}.** {ui.md_escape(value)}")
        evidence = analytics.evidence_dict(alert["evidence"])
        st.dataframe(ui.evidence_table(evidence), hide_index=True, width="stretch")
        for key, value in evidence.items():
            if isinstance(value, list) and value:
                st.caption(key.replace("_", " ").capitalize())
                st.dataframe(pd.DataFrame(value), hide_index=True, width="stretch")
        st.caption(f"Reference: {alert['reference']}")
    with right:
        st.markdown("**Explanation**")
        result = explanation_panel(alert, names)
        _decision_form(alert, result)


def render() -> None:
    filters: analytics.Filters = st.session_state.filters
    st.title("Alerts to review", anchor=False)
    st.caption(
        f"{ui.scope_label(filters)} · {ui.period_label(filters)}. Each alert is a risk "
        "indicator computed from the published data, ordered by severity and amount at "
        "stake; most have a legitimate explanation."
    )
    c1, c2, c3 = st.columns([2, 3, 1])
    categories = c1.multiselect("Category", list(CATEGORIES.values()), default=DEFAULT_CATEGORIES)
    labels = {t.label: code for code, t in TYPES.items()}
    chosen = c2.multiselect("Alert", list(labels))
    severities = c3.multiselect("Severity", ["high", "medium", "low"])
    frame = _alerts(filters, tuple(categories), tuple(labels[c] for c in chosen), tuple(severities))
    if frame.empty:
        st.info("No alert matches these filters.")
        return
    st.caption(f"{len(frame):,} alerts shown (the 500 with the highest priority at most).")
    table = pd.DataFrame(
        {
            "Priority": frame["priority"].round(1),
            "Severity": frame["severity"].str.capitalize(),
            "Alert": frame["label"],
            "What was found": frame["title"],
            "Buyer": frame["buyer_name"],
            "At stake (€)": frame["amount_at_stake"].round(0),
            "Date": pd.to_datetime(frame["reference_date"]).dt.date,
        }
    )
    selection = st.dataframe(
        table,
        hide_index=True,
        width="stretch",
        height=320,
        on_select="rerun",
        selection_mode="single-row",
        column_config={
            "At stake (€)": st.column_config.NumberColumn(format="%,.0f"),
            "What was found": st.column_config.TextColumn(width="large"),
        },
    )
    rows = selection.selection.rows if selection else []
    if not rows:
        st.info("Select an alert to see its evidence, an explanation and record a decision.")
        return
    st.divider()
    _detail(frame.iloc[rows[0]].to_dict())


render()
