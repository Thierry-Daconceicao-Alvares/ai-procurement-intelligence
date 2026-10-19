"""Alert catalogue: categories, titles, recommended actions and priorities.

The detectors (sql/06_alerts.sql and the classifier) produce the evidence of
each alert. This module turns it into what a reviewer reads: a one-line title
with the key figures, the action to take, and a priority score to order the
review queue. Titles and actions are deterministic: they never come from the
language model, which only adds an optional explanation (explain.py).
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable
from dataclasses import dataclass

import duckdb
import pandas as pd

from .formatting import eur, pct, ratio_phrase

CATEGORIES = {
    "price": "Price and amount drift",
    "leakage": "Value leakage and competition",
    "supplier": "Supplier risk",
    "classification": "Spend classification",
    "quality": "Data quality",
}

SEVERITY_POINTS = {"high": 4.0, "medium": 2.0, "low": 0.0}


@dataclass(frozen=True)
class AlertType:
    code: str
    category: str
    label: str
    action: str
    reference: str
    title: Callable[[dict], str]


def _increase_title(e: dict) -> str:
    return (
        f"Amount up {pct(e['increase_ratio'])} after {e['modification_count']} "
        f"modification{'s' if e['modification_count'] > 1 else ''} "
        f"({eur(e['initial_amount'])} to {eur(e['current_amount'])})"
    )


def _amount_title(e: dict) -> str:
    issue = e.get("amount_issue")
    amount = e["reported_amount"]
    if issue == "placeholder":
        exact = f"{amount:,.2f}".removesuffix(".00")
        return f"Reported amount €{exact} looks like a placeholder: excluded from totals"
    if issue == "out_of_bounds":
        return (
            f"Reported amount {eur(amount)} is outside the plausible range of €100 to €10bn: "
            "excluded from totals"
        )
    if issue == "default_ceiling":
        return (
            f"Same amount of {eur(amount)} reported for {e['same_amount_contracts']} contracts "
            f"in {e['same_amount_divisions']} categories, "
            f"{ratio_phrase(amount / e['buyer_p90_amount'])} the buyer's 90th-percentile "
            "contract: "
            "excluded from totals"
        )
    phrase = ratio_phrase(e["ratio_to_expected"])
    title = (
        f"Reported amount {eur(amount)} is {phrase} the usual amount of "
        f"comparable contracts (about {eur(e['expected_amount'])})"
    )
    return title + (": excluded from totals" if e.get("excluded_from_totals") else "")


def _splitting_title(e: dict) -> str:
    return (
        f"{e['contract_count']} contracts in {e['year']}, each below the "
        f"{eur(e['eu_threshold'])} EU threshold, together {eur(e['total_amount'])}"
    )


def _duplicate_title(e: dict) -> str:
    removed = e["records"] - 1
    return (
        f"Contract of {eur(e['amount'])} declared {e['records']} times: "
        f"{removed} record{'s' if removed > 1 else ''} removed from totals"
    )


def _joint_title(e: dict) -> str:
    return (
        f"Joint purchase of {eur(e['amount'])} published by {e['buyers']} buyers, each with "
        f"the full amount: {e['records']} records counted once"
    )


def _framework_title(e: dict) -> str:
    return (
        f"One framework agreement of {eur(e['amount'])} published once per supplier: "
        f"{e['records']} records merged ({e['holders']} suppliers)"
    )


TYPES = {
    t.code: t
    for t in [
        AlertType(
            "amount_increase",
            "price",
            "Amount increased by modifications",
            "Check the legal ground of each modification (art. R. 2194-1 to R. 2194-9) and "
            "whether the change still fits the initial need; if the need has changed, "
            "consider a new competition.",
            "Code de la commande publique, art. R. 2194-3, R. 2194-5 and R. 2194-8",
            _increase_title,
        ),
        AlertType(
            "atypical_amount",
            "price",
            "Amount far from comparable contracts",
            "Compare the amount with similar contracts and the buyer's estimate; check "
            "whether the scope, the duration or a reporting error explains the gap.",
            "Model of expected amounts (amount_model.py)",
            _amount_title,
        ),
        AlertType(
            "implausible_amount",
            "quality",
            "Implausible reported amount",
            "Ask the buyer to correct the published amount; until then the contract is "
            "excluded from totals.",
            "Amount checks (sql/04_amount_quality.sql) and model of expected amounts "
            "(amount_model.py)",
            _amount_title,
        ),
        AlertType(
            "single_bid",
            "leakage",
            "Single offer in a formal tender",
            "Review why competition failed (specifications too narrow, short deadline, "
            "market structure) before the next tender for this need.",
            "Single-bidder indicator of the EU Single Market Scoreboard",
            lambda e: (
                f"Single offer in a procedure of {eur(e['awarded_amount'])} "
                f"({e['procedure_type'].lower()})"
            ),
        ),
        AlertType(
            "buyer_single_bid_rate",
            "leakage",
            "High single-offer rate for this buyer",
            "Review the buyer's tendering practices: market sounding, lot design, "
            "deadlines and publicity.",
            "Single-bidder indicator of the EU Single Market Scoreboard",
            lambda e: (
                f"{pct(e['single_bid_rate'])} of {e['procedures']} competitive procedures "
                f"received a single offer (expected {pct(e['expected_rate'])})"
            ),
        ),
        AlertType(
            "non_competitive_award",
            "leakage",
            "Award without competition above the threshold",
            "Check that one of the legal exceptions applies and is documented (urgency, "
            "unsuccessful tender, exclusive rights: art. R. 2122-1 to R. 2122-11).",
            "Code de la commande publique, art. R. 2122-8",
            lambda e: (
                f"Awarded without publication or competition: {eur(e['awarded_amount'])} "
                f"(threshold {eur(e['no_competition_threshold'])})"
            ),
        ),
        AlertType(
            "possible_splitting",
            "leakage",
            "Possible splitting below the EU threshold",
            "Check whether these purchases meet one homogeneous need over the year; if so, "
            "their total value sets the procedure (art. R. 2121-1 to R. 2121-9).",
            "Code de la commande publique, art. R. 2121-1 and following",
            _splitting_title,
        ),
        AlertType(
            "supplier_dependency",
            "supplier",
            "Dependency on one supplier",
            "Assess the supply risk and the market: alternative suppliers, lot design, "
            "length of the next contract.",
            "Herfindahl-Hirschman index of the buyer's spend in the category",
            lambda e: (
                f"One supplier holds {pct(e['top_supplier_share'])} of "
                f"{eur(e['category_amount'])} spent in this category "
                f"({e['suppliers']} supplier{'s' if e['suppliers'] > 1 else ''})"
            ),
        ),
        AlertType(
            "unusual_supplier_activity",
            "supplier",
            "Supplier activity unusual for the category",
            "Check that the holder identifier is right and that the supplier can deliver "
            "this category (subcontracting, intermediary).",
            "Pairs of NAF and CPV divisions observed in the data",
            lambda e: (
                f"Main activity of the supplier ({e['naf_code']}) is rare for this category: "
                f"{pct(e['share_of_naf_division_contracts'], 2)} of its sector's contracts"
            ),
        ),
        AlertType(
            "supplier_ceased_before_award",
            "supplier",
            "Supplier ceased before the award",
            "Check the holder identifier with the buyer: a ceased company cannot perform "
            "a new contract.",
            "Base Sirene (INSEE)",
            lambda e: f"Supplier recorded as ceased {e['days_between']} days before the award",
        ),
        AlertType(
            "newly_created_supplier",
            "supplier",
            "Newly created supplier",
            "Check the supplier's capacities and guarantees (references, financial "
            "standing) as required for this amount.",
            "Base Sirene (INSEE)",
            lambda e: (
                f"Supplier created {e['company_age_days']} days before an award of "
                f"{eur(e['awarded_amount'])}"
            ),
        ),
        AlertType(
            "duplicate_declarations",
            "quality",
            "Contract declared several times",
            "Report the duplicate records to the data publisher; only one is counted here.",
            "Deduplication rule (sql/03_duplicate_candidates.sql)",
            _duplicate_title,
        ),
        AlertType(
            "framework_split_records",
            "quality",
            "Framework agreement published per supplier",
            "Nothing to correct for the buyer; the records are merged so that the maximum "
            "amount is counted once.",
            "Deduplication rule (sql/03_duplicate_candidates.sql)",
            _framework_title,
        ),
        AlertType(
            "joint_purchase_records",
            "quality",
            "Joint purchase published by each member",
            "Nothing to correct for the buyers; the records are merged so that the amount is "
            "counted once. To report spend per member, ask the coordinator of the purchasing "
            "group for each member's share.",
            "Deduplication rule (sql/03_duplicate_candidates.sql)",
            _joint_title,
        ),
        AlertType(
            "cpv_mismatch",
            "classification",
            "CPV code probably wrong",
            "Confirm the category from the description and correct the CPV code, so that "
            "the spend is reported in the right category.",
            "Text classifier (classifier.py)",
            lambda e: (
                f"Declared division {e['declared_division']} ({e['declared_label']}), but the "
                f"description points to {e['predicted_division']} ({e['predicted_label']}), "
                f"probability {pct(e['predicted_probability'])}"
            ),
        ),
    ]
}


def alert_id(alert_type: str, key: str) -> str:
    return hashlib.sha1(f"{alert_type}|{key}".encode()).hexdigest()[:16]


def priority(severity: str, amount_at_stake: float | None) -> float:
    """Severity points plus the order of magnitude of the amount at stake:
    a high-severity alert on 1 million euros scores 4 + 6 = 10."""
    stake = max(amount_at_stake or 0.0, 1_000.0)
    return round(SEVERITY_POINTS[severity] + math.log10(stake), 3)


def cpv_mismatch_alerts(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Alerts for contracts whose declared CPV division is very unlikely."""
    from .classifier import FLAG_MAX_DECLARED_PROBA, FLAG_MIN_PROBA

    return con.execute(
        f"""
        SELECT
            'cpv_mismatch' AS alert_type,
            CASE WHEN p.predicted_probability >= 0.97 THEN 'medium' ELSE 'low' END AS severity,
            f.uid,
            f.buyer_siret,
            f.lead_holder_key AS holder_key,
            f.cpv_division,
            f.notification_date AS reference_date,
            coalesce(f.awarded_amount, 0) AS amount_at_stake,
            to_json({{
                'declared_cpv_code': f.cpv_code,
                'declared_division': p.declared_division,
                'declared_label': d1.cpv_label,
                'declared_probability': p.declared_probability,
                'predicted_division': p.predicted_division,
                'predicted_label': d2.cpv_label,
                'predicted_probability': p.predicted_probability,
                'awarded_amount': f.awarded_amount
            }}) AS evidence
        FROM int_cpv_predictions AS p
        JOIN fct_contracts AS f USING (uid)
        LEFT JOIN dim_cpv AS d1 ON d1.cpv_code = p.declared_division || '000000'
        LEFT JOIN dim_cpv AS d2 ON d2.cpv_code = p.predicted_division || '000000'
        WHERE p.predicted_division <> p.declared_division
            AND p.predicted_probability >= {FLAG_MIN_PROBA}
            AND p.declared_probability <= {FLAG_MAX_DECLARED_PROBA}
        """
    ).fetchdf()


def build_alerts(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Create fct_alerts from int_alerts and the classifier flags."""
    frames = [con.execute("SELECT * FROM int_alerts").fetchdf()]
    if con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = 'int_cpv_predictions'"
    ).fetchone()[0]:
        frames.append(cpv_mismatch_alerts(con))
    alerts = pd.concat(frames, ignore_index=True)
    evidence = alerts["evidence"].map(json.loads)
    key = alerts["uid"].fillna("") + "|" + alerts["buyer_siret"].fillna("") + "|"
    key = key + alerts["holder_key"].fillna("") + "|" + alerts["cpv_division"].fillna("")
    key = key + "|" + alerts["reference_date"].astype(str)
    alerts["alert_id"] = [alert_id(t, k) for t, k in zip(alerts["alert_type"], key, strict=True)]
    alerts["category"] = alerts["alert_type"].map(lambda t: CATEGORIES[TYPES[t].category])
    alerts["label"] = alerts["alert_type"].map(lambda t: TYPES[t].label)
    alerts["title"] = [
        TYPES[t].title(e) for t, e in zip(alerts["alert_type"], evidence, strict=True)
    ]
    alerts["recommended_action"] = alerts["alert_type"].map(lambda t: TYPES[t].action)
    alerts["reference"] = alerts["alert_type"].map(lambda t: TYPES[t].reference)
    alerts["priority"] = [
        priority(s, a) for s, a in zip(alerts["severity"], alerts["amount_at_stake"], strict=True)
    ]
    alerts = alerts.drop_duplicates("alert_id")
    con.register("alerts", alerts)
    con.execute("CREATE OR REPLACE TABLE fct_alerts AS SELECT * FROM alerts ORDER BY priority DESC")
    con.unregister("alerts")
    return alerts
