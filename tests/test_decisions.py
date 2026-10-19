"""The decision log is an append-only audit trail."""

from __future__ import annotations

import pytest

from procurement_intel.decisions import DECISIONS, Decision, DecisionLog, evidence_hash


def decision(**values) -> Decision:
    fields = {
        "alert_id": "a1",
        "alert_type": "single_bid",
        "decision": DECISIONS[0],
        "reason": "Specifications too narrow",
        "recommendation": "Review the lot design before the next tender.",
        "explanation_source": "template",
        "evidence_sha256": evidence_hash({"b": 1, "a": 2}),
        "decided_by": "T. Reviewer",
    }
    fields.update(values)
    return Decision(**fields)


def test_evidence_hash_is_canonical():
    assert evidence_hash({"a": 1, "b": [1, 2]}) == evidence_hash('{"b": [1, 2], "a": 1}')
    assert evidence_hash({"a": 1}) != evidence_hash({"a": 2})


@pytest.mark.parametrize(
    "values",
    [{"decision": "Maybe"}, {"reason": "  "}, {"decided_by": ""}],
)
def test_incomplete_decisions_are_refused(values):
    with pytest.raises(ValueError):
        decision(**values)


def test_decisions_are_appended_and_the_latest_wins(tmp_path):
    log = DecisionLog(tmp_path / "decisions" / "log.sqlite")
    log.record(decision())
    log.record(decision(decision=DECISIONS[1], reason="Urgency documented"))
    log.record(decision(alert_id="a2"))
    assert len(log.history()) == 3
    assert len(log.history("a1")) == 2
    latest = log.latest().set_index("alert_id")
    assert latest.loc["a1", "decision"] == DECISIONS[1]
    assert log.to_csv().decode().splitlines()[0].startswith("decision_id,alert_id")
    # The file persists: a new log on the same path sees the decisions.
    assert len(DecisionLog(tmp_path / "decisions" / "log.sqlite").history()) == 3


def test_memory_log_for_the_public_demo():
    log = DecisionLog(None)
    log.record(decision())
    assert len(log.history()) == 1
