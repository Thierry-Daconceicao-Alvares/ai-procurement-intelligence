"""Alert catalogue: every type has a readable title, an action and a reference."""

from __future__ import annotations

import pytest

from procurement_intel.alerts import CATEGORIES, TYPES, alert_id, priority
from procurement_intel.explain import LEGITIMATE

EVIDENCE = {
    "amount_increase": {
        "increase_ratio": 0.62,
        "modification_count": 2,
        "initial_amount": 1.2e6,
        "current_amount": 1.944e6,
    },
    "atypical_amount": {
        "reported_amount": 4.5e6,
        "expected_amount": 1.1e5,
        "ratio_to_expected": 40.9,
    },
    "implausible_amount": {
        "reported_amount": 999_999_999.0,
        "amount_issue": "placeholder",
        "expected_amount": 2.4e6,
        "ratio_to_expected": 416.0,
        "excluded_from_totals": True,
    },
    "single_bid": {"awarded_amount": 3.2e6, "procedure_type": "Open tender"},
    "buyer_single_bid_rate": {"single_bid_rate": 0.48, "procedures": 210, "expected_rate": 0.19},
    "non_competitive_award": {"awarded_amount": 180_000.0, "no_competition_threshold": 40_000},
    "possible_splitting": {
        "contract_count": 4,
        "year": 2024,
        "eu_threshold": 221_000,
        "total_amount": 310_000.0,
    },
    "supplier_dependency": {"top_supplier_share": 0.83, "category_amount": 2.3e6, "suppliers": 2},
    "unusual_supplier_activity": {"naf_code": "56.10A", "share_of_naf_division_contracts": 0.0012},
    "supplier_ceased_before_award": {"days_between": 412},
    "newly_created_supplier": {"company_age_days": 96, "awarded_amount": 750_000.0},
    "duplicate_declarations": {"records": 3, "amount": 85_000.0},
    "framework_split_records": {"records": 4, "holders": 4, "amount": 2e6},
    "joint_purchase_records": {"records": 14, "buyers": 14, "amount": 3e8},
    "cpv_mismatch": {
        "declared_division": "45",
        "declared_label": "Construction work",
        "predicted_division": "15",
        "predicted_label": "Food, beverages",
        "predicted_probability": 0.96,
    },
}


def test_every_type_has_a_sample_evidence():
    assert set(EVIDENCE) == set(TYPES)


@pytest.mark.parametrize("code", sorted(TYPES))
def test_titles_actions_and_explanations(code):
    kind = TYPES[code]
    title = kind.title(EVIDENCE[code])
    assert title and "{" not in title
    assert "nan" not in title.split() and "None" not in title
    assert kind.category in CATEGORIES
    assert kind.action.endswith(".")
    assert kind.reference
    assert code in LEGITIMATE


@pytest.mark.parametrize(
    ("issue", "fragment"),
    [
        ("placeholder", "€999,999,999 looks like a placeholder"),
        ("out_of_bounds", "outside the plausible range"),
        ("default_ceiling", "reported for 21 contracts in 12 categories"),
        ("far_from_expected", "times the usual amount"),
    ],
)
def test_implausible_amount_titles_name_the_reason(issue, fragment):
    evidence = {
        "reported_amount": 999_999_999.0 if issue == "placeholder" else 1e9,
        "amount_issue": issue,
        "expected_amount": 3.4e6,
        "ratio_to_expected": 294.0,
        "excluded_from_totals": True,
        "same_amount_contracts": 21,
        "same_amount_divisions": 12,
        "buyer_p90_amount": 2.5e6,
    }
    title = TYPES["implausible_amount"].title(evidence)
    assert fragment in title
    assert title.endswith("excluded from totals")


def test_priority_orders_by_severity_then_amount():
    assert priority("high", 1e6) == 10.0
    assert priority("medium", 1e6) == 8.0
    assert priority("high", None) == priority("high", 10) == 7.0
    assert priority("medium", 1e9) > priority("high", 1e6)


def test_alert_ids_are_stable_and_distinct():
    assert alert_id("single_bid", "u1|b|h|45|2024-01-01") == alert_id(
        "single_bid", "u1|b|h|45|2024-01-01"
    )
    assert alert_id("single_bid", "u1") != alert_id("atypical_amount", "u1")
    assert len(alert_id("single_bid", "u1")) == 16
