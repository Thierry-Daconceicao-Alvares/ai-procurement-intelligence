"""Explanations: figures are verified, and a template works without the model."""

from __future__ import annotations

import json

from procurement_intel.alerts import TYPES
from procurement_intel.explain import (
    AlertExplanation,
    Briefing,
    BriefingSection,
    alert_context,
    explain_alert,
    template_briefing,
    template_explanation,
)
from procurement_intel.llm import Usage

ALERT = {
    "alert_type": "single_bid",
    "category": "Value leakage and competition",
    "severity": "high",
    "title": "Single offer in a procedure of €3.4M (open tender)",
    "evidence": json.dumps(
        {
            "awarded_amount": 3_412_550.0,
            "offers_received": 1,
            "procedure_type": "Open tender",
            "eu_threshold": 221_000,
        }
    ),
}


class FakeGemini:
    model = "fake-model"

    def __init__(self, answer):
        self.answer = answer
        self.prompts: list[str] = []

    def generate(self, system, user, schema):
        self.prompts.append(user)
        return self.answer, Usage(prompt_tokens=420, output_tokens=95), 0.8


def test_context_carries_the_evidence_and_the_action():
    context = alert_context(ALERT, {"buyer_name": "COMMUNE DE TEST", "description": "Voirie"})
    assert context["evidence"]["awarded_amount"] == 3_412_550.0
    assert context["recommended_action"] == TYPES["single_bid"].action
    assert context["buyer"] == "COMMUNE DE TEST"


def test_verified_explanation():
    answer = AlertExplanation(
        summary="One offer was received for a €3.4M open tender.",
        why_it_matters="Weak competition can raise prices.",
        checks=["Check the deadline.", "Check the lot design."],
        legitimate_explanations="The market may have few able suppliers.",
    )
    gemini = FakeGemini(answer)
    result = explain_alert(gemini, alert_context(ALERT))
    assert result.verified and result.source == "fake-model"
    assert result.to_record()["prompt_tokens"] == 420
    assert "3412550" in gemini.prompts[0]


def test_invented_figure_is_flagged():
    answer = AlertExplanation(
        summary="One offer for €3.4M, 35% above similar tenders.",
        why_it_matters="Prices may be higher.",
        checks=["Compare with 12 similar contracts."],
        legitimate_explanations="Few suppliers.",
    )
    result = explain_alert(FakeGemini(answer), alert_context(ALERT))
    assert not result.verified
    assert result.unverified == ["35%", "12"]


def test_templates_need_no_model():
    for code in TYPES:
        context = {"title": "Something was found", "buyer": "COMMUNE DE TEST"}
        result = template_explanation(context, code)
        assert result.source == "template"
        assert result.content.summary.startswith("COMMUNE DE TEST: something was found")
    facts = {
        "scope": "All French public buyers",
        "period": "2019 to 2026",
        "awarded_value": 1.16e12,
        "contracts": 1_526_229,
        "suppliers": 160_677,
        "top10_supplier_share": 0.09,
        "sme_share": 0.3,
        "single_bid_rate": 0.2,
        "non_competitive_share": 0.07,
        "top_categories": [{"label": "Construction work", "value": 3.2e11, "share": 0.28}],
        "alerts": [{"label": "Single offer in a formal tender", "count": 18_000}],
    }
    briefing = template_briefing(facts).content
    assert isinstance(briefing, Briefing)
    assert all(isinstance(section, BriefingSection) for section in briefing.sections)
    assert "€1.16tn" in briefing.sections[0].bullets[0]
