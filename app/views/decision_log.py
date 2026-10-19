"""Decision log: every decision recorded on an alert, exportable."""

from __future__ import annotations

import streamlit as st
import ui


def render() -> None:
    st.title("Decision log", anchor=False)
    log = ui.decision_log()
    history = log.history()
    if ui.settings().public_demo:
        st.caption(
            "In the public demo, decisions are kept for your browser session only. Export them "
            "before leaving."
        )
    st.markdown(
        "Each decision keeps the recommendation as accepted, the source of the explanation "
        "the reviewer saw (template or model and prompt version) and the SHA-256 of the "
        "evidence at the time of the decision. Decisions are appended, never overwritten."
    )
    if history.empty:
        st.info("No decision recorded yet. Record one from the review queue.")
        st.page_link("views/alerts.py", label="Open the review queue", icon=":material/rule:")
        return
    counts = history.groupby("decision").size().reset_index(name="decisions")
    st.dataframe(counts, hide_index=True)
    st.dataframe(history, hide_index=True, width="stretch")
    st.download_button(
        "Export the decision log (CSV)",
        data=log.to_csv(),
        file_name="decision_log.csv",
        mime="text/csv",
        icon=":material/download:",
    )


render()
