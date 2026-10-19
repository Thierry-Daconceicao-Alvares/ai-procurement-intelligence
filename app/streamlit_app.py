"""Procurement Intelligence: Streamlit application.

Run with:  streamlit run app/streamlit_app.py
The application reads the Parquet marts of data/marts/ (built by
`python -m procurement_intel build`) and never the raw data.
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

try:
    import procurement_intel  # noqa: F401
except ModuleNotFoundError:
    # Run from a clone without installing the package (Streamlit Community Cloud
    # installs requirements.txt only).
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import ui

st.set_page_config(
    page_title="Procurement Intelligence",
    page_icon=":material/query_stats:",
    layout="wide",
)

if not (ui.settings().marts_dir / "contracts").exists():
    st.title("Procurement Intelligence", anchor=False)
    st.error("The marts are missing. Build them with `python -m procurement_intel build`.")
    st.stop()

st.session_state.filters = ui.sidebar_filters()

pages = st.navigation(
    {
        "Analysis": [
            st.Page("views/overview.py", title="Spend overview", icon=":material/dashboard:"),
            st.Page(
                "views/categories.py", title="Categories and markets", icon=":material/category:"
            ),
            st.Page("views/suppliers.py", title="Suppliers and risk", icon=":material/factory:"),
        ],
        "Review": [
            st.Page("views/alerts.py", title="Alerts to review", icon=":material/rule:"),
            st.Page(
                "views/classification.py", title="Spend classification", icon=":material/label:"
            ),
            st.Page("views/decision_log.py", title="Decision log", icon=":material/history:"),
        ],
        "About": [
            st.Page(
                "views/data_quality.py",
                title="Data quality and method",
                icon=":material/fact_check:",
            ),
        ],
    }
)
pages.run()
