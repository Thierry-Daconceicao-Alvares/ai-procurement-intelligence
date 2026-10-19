"""Every figure written by the language model must come from the evidence."""

from __future__ import annotations

from procurement_intel.verify import evidence_numbers, text_numbers, unverified_figures

EVIDENCE = {
    "awarded_amount": 3_412_550.0,
    "single_bid_rate": 0.4812,
    "procedures": 210,
    "notification_date": "2024-03-15",
    "nested": [{"value": "125000"}],
}


def test_evidence_numbers_walk_nested_values_and_dates():
    numbers = evidence_numbers(EVIDENCE)
    assert {3_412_550.0, 0.4812, 210.0, 2024.0, 3.0, 15.0, 125_000.0} <= set(numbers)


def test_rounded_figures_are_accepted():
    text = "A single offer for €3.4M; 48% of 210 procedures, in March 2024; €125k in total."
    assert unverified_figures(text, EVIDENCE) == []


def test_invented_figures_are_reported():
    text = "The amount of €5.1M is 81% above the market, across 2,300 contracts."
    assert unverified_figures(text, EVIDENCE) == ["€5.1M", "81%", "2,300"]


def test_codes_and_legal_references_are_not_figures():
    text = "Article R. 2194-8 applies; CPV 45210000; NAF 56.10A; three checks."
    assert text_numbers(text) == []
    assert unverified_figures("Make 2 checks.", EVIDENCE) == []


def test_trillions_and_billions():
    assert unverified_figures("€1.16tn awarded, €472bn", {"a": 1.1647e12, "b": 4.723e11}) == []
