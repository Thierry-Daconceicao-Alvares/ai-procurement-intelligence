"""Decision log: what a reviewer decided about each alert, and on which basis.

Every decision records the alert, the decision and its reason, the
recommendation as accepted or edited, the source of the explanation the
reviewer saw (template or model and prompt version) and the SHA-256 of the
evidence at the time of the decision. Decisions are appended, never
overwritten: the log is an audit trail.

Storage is SQLite: a file in data/decisions/ for a local or internal
deployment, or memory only for a visitor of the public demo.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

DECISIONS = ("Action needed", "No action: explained", "Data error", "Escalated")

SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    decision_id INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id TEXT NOT NULL,
    alert_type TEXT NOT NULL,
    decision TEXT NOT NULL,
    reason TEXT NOT NULL,
    recommendation TEXT NOT NULL,
    explanation_source TEXT NOT NULL,
    prompt_version TEXT,
    evidence_sha256 TEXT NOT NULL,
    decided_by TEXT NOT NULL,
    decided_at TEXT NOT NULL
)
"""


def evidence_hash(evidence: object) -> str:
    """SHA-256 of the evidence in canonical JSON form."""
    if isinstance(evidence, str):
        evidence = json.loads(evidence)
    canonical = json.dumps(evidence, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass
class Decision:
    alert_id: str
    alert_type: str
    decision: str
    reason: str
    recommendation: str
    explanation_source: str
    evidence_sha256: str
    decided_by: str
    prompt_version: str | None = None
    decided_at: str = field(
        default_factory=lambda: datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    )

    def __post_init__(self) -> None:
        if self.decision not in DECISIONS:
            raise ValueError(f"Unknown decision {self.decision!r}")
        if not self.reason.strip():
            raise ValueError("A decision needs a reason.")
        if not self.decided_by.strip():
            raise ValueError("A decision needs the name of the reviewer.")


class DecisionLog:
    def __init__(self, path: Path | None = None) -> None:
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(
            str(path) if path is not None else ":memory:", check_same_thread=False
        )
        self._con.execute(SCHEMA)
        self._con.commit()

    def record(self, decision: Decision) -> int:
        values = asdict(decision)
        columns = ", ".join(values)
        placeholders = ", ".join("?" for _ in values)
        cursor = self._con.execute(
            f"INSERT INTO decisions ({columns}) VALUES ({placeholders})", list(values.values())
        )
        self._con.commit()
        return int(cursor.lastrowid)

    def history(self, alert_id: str | None = None) -> pd.DataFrame:
        query = "SELECT * FROM decisions"
        params: list[str] = []
        if alert_id:
            query += " WHERE alert_id = ?"
            params.append(alert_id)
        return pd.read_sql_query(query + " ORDER BY decision_id", self._con, params=params)

    def latest(self) -> pd.DataFrame:
        """The last decision on each alert."""
        return pd.read_sql_query(
            """
            SELECT * FROM decisions
            WHERE decision_id IN (SELECT max(decision_id) FROM decisions GROUP BY alert_id)
            ORDER BY decision_id DESC
            """,
            self._con,
        )

    def to_csv(self) -> bytes:
        return self.history().to_csv(index=False).encode("utf-8")
