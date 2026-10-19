"""Shared building blocks of the application: data access, filters, tiles and
the safe display of text that comes from the data."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd
import streamlit as st

from procurement_intel import analytics
from procurement_intel.config import Settings
from procurement_intel.decisions import DecisionLog
from procurement_intel.formatting import compact, eur, pct, ratio_phrase

REPOSITORY_URL = "https://github.com/Thierry-Daconceicao-Alvares/ai-procurement-intelligence"
SOURCE_NOTE = (
    "Source: Données essentielles de la commande publique (consolidated by decp.info, "
    "Licence Ouverte 2.0) and Base Sirene (INSEE), snapshot of 19 October 2026. "
    "Alerts are risk indicators to review, not findings of wrongdoing."
)

_MARKDOWN_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+\-.!|~$<>])")


def md_escape(text: object) -> str:
    """Show text from the data literally: descriptions and names must not be
    read as Markdown, HTML or LaTeX."""
    return _MARKDOWN_SPECIAL.sub(r"\\\1", str(text))


@st.cache_resource(show_spinner=False)
def settings() -> Settings:
    return Settings.from_env()


@st.cache_resource(show_spinner=False)
def _database():
    return analytics.connect(settings().marts_dir)


def connection():
    """A cursor of the shared database for each query: a DuckDB connection must
    not run queries from several threads (sessions and pages) at the same time,
    or one query may read the result of another."""
    return _database().cursor()


@st.cache_data(show_spinner=False)
def reference_lists() -> dict:
    con = connection()
    regions = con.execute(
        """SELECT DISTINCT buyer_region_code AS code, buyer_region AS name FROM buyers
           WHERE buyer_region_code IS NOT NULL AND buyer_region IS NOT NULL ORDER BY name"""
    ).fetchdf()
    categories = (
        con.execute(
            "SELECT DISTINCT buyer_category FROM buyers WHERE buyer_category IS NOT NULL ORDER BY 1"
        )
        .fetchdf()["buyer_category"]
        .tolist()
    )
    return {
        "regions": dict(zip(regions["code"], regions["name"], strict=True)),
        "buyer_categories": categories,
    }


@st.cache_data(show_spinner=False)
def metrics(name: str) -> dict:
    path = settings().metrics_dir / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def decision_log() -> DecisionLog:
    """A file for a local or internal deployment; one log per browser session
    for the public demo, deleted with the session."""
    if settings().public_demo:
        if "decision_log" not in st.session_state:
            st.session_state.decision_log = DecisionLog(None)
        return st.session_state.decision_log
    return _shared_log(settings().data_dir / "decisions" / "decisions.sqlite")


@st.cache_resource(show_spinner=False)
def _shared_log(path: Path) -> DecisionLog:
    return DecisionLog(path)


def sidebar_filters() -> analytics.Filters:
    lists = reference_lists()
    with st.sidebar:
        st.markdown("### Filters")
        years = st.slider("Notification year", 2019, 2026, (2019, 2026))
        regions = st.multiselect(
            "Buyer region", list(lists["regions"]), format_func=lambda c: lists["regions"][c]
        )
        categories = st.multiselect("Buyer category", lists["buyer_categories"])
        types = st.multiselect("Contract type", ["Works", "Services", "Supplies"])
        query = st.text_input("Buyer", placeholder="Name or SIRET, e.g. Ville de Lyon")
        buyer = None
        if query.strip():
            matches = analytics.buyer_search(connection(), query.strip())
            if matches.empty:
                st.caption("No buyer found.")
            else:
                options = dict(zip(matches["buyer_siret"], matches["buyer_name"], strict=True))
                buyer = st.selectbox(
                    "Matching buyers", list(options), format_func=lambda s: options[s]
                )
        frameworks = st.toggle(
            "Include framework agreements",
            value=True,
            help="Framework agreements report a maximum amount, which is not spent in full.",
        )
        st.divider()
        st.caption(SOURCE_NOTE)
        st.caption(f"[Source code and method]({REPOSITORY_URL})")
    return analytics.Filters(
        first_year=years[0],
        last_year=years[1],
        regions=tuple(regions),
        buyer_categories=tuple(categories),
        contract_types=tuple(types),
        buyer_siret=buyer,
        include_frameworks=frameworks,
    )


def scope_label(filters: analytics.Filters) -> str:
    lists = reference_lists()
    parts = []
    if filters.buyer_siret:
        name = (
            connection()
            .execute("SELECT buyer_name FROM buyers WHERE buyer_siret = ?", [filters.buyer_siret])
            .fetchone()
        )
        parts.append(name[0] if name else filters.buyer_siret)
    if filters.regions:
        parts.append(", ".join(lists["regions"].get(r, r) for r in filters.regions))
    if filters.buyer_categories:
        parts.append(", ".join(filters.buyer_categories))
    if filters.contract_types:
        parts.append(", ".join(filters.contract_types))
    if not filters.include_frameworks:
        parts.append("excluding framework agreements")
    return " · ".join(parts) if parts else "All French public buyers"


def period_label(filters: analytics.Filters) -> str:
    last = f"{filters.last_year}" + (" (January to September)" if filters.last_year == 2026 else "")
    return f"{filters.first_year} to {last}" if filters.first_year != filters.last_year else last


def tiles(items: list[tuple[str, str, str | None]]) -> None:
    """A row of stat tiles: (label, value, help)."""
    columns = st.columns(len(items))
    for column, (label, value, help_text) in zip(columns, items, strict=True):
        column.metric(label, value, help=help_text, border=True)


def money(value: float | None) -> str:
    return eur(value)


def count(value: float | None) -> str:
    return compact(value)


def percent(value: float | None, digits: int = 0) -> str:
    return pct(value, digits)


def _evidence_value(key: str, value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if not isinstance(value, int | float):
        return str(value)
    if key == "ratio_to_expected":
        return f"{ratio_phrase(value)} the expected amount"
    if "amount" in key and "contracts" not in key and "divisions" not in key:
        return f"€{value:,.2f}".removesuffix(".00") if value >= 1e7 else f"€{value:,.0f}"
    if "value" in key or "threshold" in key:
        return f"€{value:,.0f}"
    if any(word in key for word in ("ratio", "share", "rate", "probability")):
        return pct(value, 1)
    return f"{value:,}" if isinstance(value, int) else f"{value:,.2f}"


def _evidence_label(key: str) -> str:
    label = key.replace("_", " ").capitalize()
    for acronym in ("EU", "CPV", "NAF", "HHI", "SME"):
        label = re.sub(rf"\b{acronym}\b", acronym, label, flags=re.IGNORECASE)
    return label


def evidence_table(evidence: dict) -> pd.DataFrame:
    """Evidence as a two-column table, with amounts, shares and ratios readable."""
    rows = [
        {"Evidence": _evidence_label(key), "Value": _evidence_value(key, value)}
        for key, value in evidence.items()
        if not isinstance(value, list) and value is not None
    ]
    return pd.DataFrame(rows)
