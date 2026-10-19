"""Shared helpers: synthetic contracts and SQL models run in memory."""

from __future__ import annotations

from datetime import date

import duckdb
import pandas as pd
import pytest

from procurement_intel.warehouse import SQL_DIR

CONTRACT_COLUMNS = {
    "uid": "VARCHAR",
    "buyer_siret": "VARCHAR",
    "buyer_name": "VARCHAR",
    "lead_holder_key": "VARCHAR",
    "cpv_code": "VARCHAR",
    "contract_type": "VARCHAR",
    "is_framework": "BOOLEAN",
    "procedure_type": "VARCHAR",
    "procedure_group": "VARCHAR",
    "initial_amount": "DOUBLE",
    "notification_date": "DATE",
    "publication_date": "DATE",
    "description": "VARCHAR",
    "offers_received": "INTEGER",
    "initial_duration_months": "INTEGER",
    "price_variation": "VARCHAR",
}


def contract(uid: str, **values) -> dict:
    """A contract of int_contracts with plausible defaults."""
    row = {
        "uid": uid,
        "buyer_siret": "21690123100011",
        "buyer_name": "COMMUNE DE TEST",
        "lead_holder_key": "FR:552081317",
        "cpv_code": "45210000",
        "contract_type": "Works",
        "is_framework": False,
        "procedure_type": "Open tender",
        "procedure_group": "Competitive",
        "initial_amount": 250_000.0,
        "notification_date": date(2024, 3, 1),
        "publication_date": date(2024, 3, 5),
        "description": "Travaux de renovation de l ecole primaire Jean Moulin",
        "offers_received": 3,
        "initial_duration_months": 12,
        "price_variation": "Firm",
    }
    row.update(values)
    return row


def load_contracts(con: duckdb.DuckDBPyConnection, rows: list[dict]) -> None:
    frame = pd.DataFrame(rows, columns=list(CONTRACT_COLUMNS))
    columns = ", ".join(f"{name} {kind}" for name, kind in CONTRACT_COLUMNS.items())
    con.execute(f"CREATE OR REPLACE TABLE int_contracts ({columns})")
    con.register("rows", frame)
    con.execute("INSERT INTO int_contracts SELECT * FROM rows")
    con.unregister("rows")


def run_sql(con: duckdb.DuckDBPyConnection, name: str) -> None:
    con.execute((SQL_DIR / f"{name}.sql").read_text(encoding="utf-8"))


@pytest.fixture
def con():
    connection = duckdb.connect()
    yield connection
    connection.close()
