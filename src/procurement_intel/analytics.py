"""Queries of the application over the Parquet marts, with DuckDB.

Every page reads the same filters (period, regions, buyer categories,
contract types, one buyer, framework agreements). They are turned into one
SQL condition with bound parameters: filter values never become SQL text.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import duckdb
import pandas as pd

VIEWS = {
    "contracts": "contracts/*.parquet",
    "contract_holders": "contract_holders/*.parquet",
    "contract_descriptions": "contract_descriptions.parquet",
    "alerts": "alerts/*.parquet",
    "buyers": "buyers.parquet",
    "suppliers": "suppliers.parquet",
    "cpv": "cpv.parquet",
    "supplier_profiles": "supplier_profiles.parquet",
    "category_markets": "category_markets.parquet",
}


def connect(marts_dir: Path, memory_limit: str = "1GB") -> duckdb.DuckDBPyConnection:
    """In-memory DuckDB with one view per mart file."""
    con = duckdb.connect()
    con.execute(f"SET memory_limit = '{memory_limit}'")
    con.execute("SET threads = 2")
    for name, pattern in VIEWS.items():
        path = (marts_dir / pattern).as_posix()
        con.execute(f"CREATE VIEW {name} AS SELECT * FROM read_parquet('{path}')")
    return con


@dataclass(frozen=True)
class Filters:
    first_year: int = 2019
    last_year: int = 2026
    regions: tuple[str, ...] = ()
    buyer_categories: tuple[str, ...] = ()
    contract_types: tuple[str, ...] = ()
    buyer_siret: str | None = None
    include_frameworks: bool = True

    def where(self, alias: str = "c") -> tuple[str, list]:
        """SQL condition on the contracts table and its parameters."""
        conditions = [f"{alias}.notification_year BETWEEN ? AND ?"]
        params: list = [self.first_year, self.last_year]
        for column, values in (
            ("buyer_region_code", self.regions),
            ("buyer_category", self.buyer_categories),
            ("contract_type", self.contract_types),
        ):
            if values:
                conditions.append(f"{alias}.{column} IN ({', '.join('?' for _ in values)})")
                params.extend(values)
        if self.buyer_siret:
            conditions.append(f"{alias}.buyer_siret = ?")
            params.append(self.buyer_siret)
        if not self.include_frameworks:
            conditions.append(f"NOT {alias}.is_framework")
        return " AND ".join(conditions), params

    @property
    def is_national(self) -> bool:
        return (
            not (self.regions or self.buyer_categories or self.contract_types or self.buyer_siret)
            and self.include_frameworks
        )


@dataclass
class Overview:
    kpis: dict
    by_year: pd.DataFrame
    by_division: pd.DataFrame
    procedures: pd.DataFrame
    single_bid_by_year: pd.DataFrame
    extras: dict = field(default_factory=dict)


def overview(con: duckdb.DuckDBPyConnection, f: Filters) -> Overview:
    where, params = f.where()
    kpis = (
        con.execute(
            f"""
        SELECT
            count(*) AS contracts,
            sum(awarded_amount) AS awarded_value,
            median(awarded_amount) AS median_value,
            count(DISTINCT buyer_siret) AS buyers,
            avg((offers_received = 1)::INT)
                FILTER (WHERE procedure_group = 'Competitive' AND offers_received IS NOT NULL)
                AS single_bid_rate,
            avg((procedure_group = 'Non-competitive')::INT) AS non_competitive_share,
            sum(awarded_amount) FILTER (WHERE is_framework) AS framework_value
        FROM contracts AS c
        WHERE {where}
        """,
            params,
        )
        .fetchdf()
        .iloc[0]
        .to_dict()
    )
    suppliers = (
        con.execute(
            f"""
        SELECT
            count(DISTINCT h.holder_key) AS suppliers,
            sum(h.allocated_amount) FILTER (WHERE h.holder_size = 'SME')
                / nullif(sum(h.allocated_amount), 0) AS sme_share
        FROM contract_holders AS h
        JOIN contracts AS c USING (uid)
        WHERE {where}
        """,
            params,
        )
        .fetchdf()
        .iloc[0]
        .to_dict()
    )
    kpis.update(suppliers)
    by_year = con.execute(
        f"""
        SELECT
            notification_year AS year,
            sum(awarded_amount) FILTER (WHERE NOT is_framework) AS contracts_value,
            sum(awarded_amount) FILTER (WHERE is_framework) AS framework_value,
            count(*) AS contracts
        FROM contracts AS c
        WHERE {where}
        GROUP BY 1 ORDER BY 1
        """,
        params,
    ).fetchdf()
    by_division = con.execute(
        f"""
        SELECT c.cpv_division, d.cpv_label AS label, sum(c.awarded_amount) AS value,
            count(*) AS contracts
        FROM contracts AS c
        LEFT JOIN cpv AS d ON d.cpv_code = c.cpv_division || '000000'
        WHERE {where} AND c.cpv_division IS NOT NULL
        GROUP BY ALL ORDER BY value DESC NULLS LAST
        """,
        params,
    ).fetchdf()
    total = by_division["value"].sum()
    by_division["share"] = by_division["value"] / total if total else 0
    procedures = con.execute(
        f"""
        SELECT procedure_type, count(*) AS contracts, sum(awarded_amount) AS value
        FROM contracts AS c WHERE {where}
        GROUP BY 1 ORDER BY contracts DESC
        """,
        params,
    ).fetchdf()
    # Competitive procedures per year: single-offer rate where the number of
    # offers is reported, and the share of procedures that report it.
    single_bid_by_year = con.execute(
        f"""
        SELECT notification_year AS year,
            avg((offers_received = 1)::INT) AS single_bid_rate,
            count(offers_received) AS procedures,
            count(offers_received) / count(*) AS reporting_share
        FROM contracts AS c
        WHERE {where} AND procedure_group = 'Competitive'
        GROUP BY 1 ORDER BY 1
        """,
        params,
    ).fetchdf()
    return Overview(kpis, by_year, by_division, procedures, single_bid_by_year)


def top_suppliers(con: duckdb.DuckDBPyConnection, f: Filters, limit: int = 15) -> pd.DataFrame:
    where, params = f.where()
    frame = con.execute(
        f"""
        WITH value AS (
            SELECT h.holder_key, sum(h.allocated_amount) AS value, count(DISTINCT uid) AS contracts,
                count(DISTINCT c.buyer_siret) AS buyers
            FROM contract_holders AS h JOIN contracts AS c USING (uid)
            WHERE {where} AND h.allocated_amount > 0
            GROUP BY h.holder_key
        )
        SELECT v.*, s.supplier_name, s.holder_size, s.naf_label, p.segment,
            v.value / sum(v.value) OVER () AS share
        FROM value AS v
        LEFT JOIN suppliers AS s USING (holder_key)
        LEFT JOIN supplier_profiles AS p USING (holder_key)
        ORDER BY v.value DESC
        LIMIT {int(limit)}
        """,
        params,
    ).fetchdf()
    return frame


def supplier_concentration(con: duckdb.DuckDBPyConnection, f: Filters) -> dict:
    """Share of the value held by the 10 largest suppliers, and supplier HHI."""
    where, params = f.where()
    row = con.execute(
        f"""
        WITH value AS (
            SELECT h.holder_key, sum(h.allocated_amount) AS value
            FROM contract_holders AS h JOIN contracts AS c USING (uid)
            WHERE {where} AND h.allocated_amount > 0
            GROUP BY h.holder_key
        ),
        ranked AS (
            SELECT value, value / sum(value) OVER () AS share,
                row_number() OVER (ORDER BY value DESC) AS rank
            FROM value
        )
        SELECT sum(share) FILTER (WHERE rank <= 10) AS top10_share, sum(share * share) AS hhi
        FROM ranked
        """,
        params,
    ).fetchone()
    return {"top10_share": row[0], "hhi": row[1]}


def alerts(
    con: duckdb.DuckDBPyConnection,
    f: Filters,
    categories: tuple[str, ...] = (),
    types: tuple[str, ...] = (),
    severities: tuple[str, ...] = (),
    limit: int = 500,
) -> pd.DataFrame:
    """Alerts of the contracts and buyers in the filters, by priority."""
    where, params = f.where()
    conditions = [
        f"""(
            (a.uid IS NOT NULL AND a.uid IN (SELECT uid FROM contracts AS c WHERE {where}))
            OR (a.uid IS NULL AND a.buyer_siret IN (
                SELECT buyer_siret FROM contracts AS c WHERE {where}))
        )"""
    ]
    params = params + params
    for column, values in (
        ("category", categories),
        ("alert_type", types),
        ("severity", severities),
    ):
        if values:
            conditions.append(f"a.{column} IN ({', '.join('?' for _ in values)})")
            params.extend(values)
    return con.execute(
        f"""
        SELECT a.*, b.buyer_name, s.supplier_name
        FROM alerts AS a
        LEFT JOIN buyers AS b USING (buyer_siret)
        LEFT JOIN suppliers AS s USING (holder_key)
        WHERE {" AND ".join(conditions)}
        ORDER BY a.priority DESC, a.alert_id
        LIMIT {int(limit)}
        """,
        params,
    ).fetchdf()


def alert_counts(con: duckdb.DuckDBPyConnection, f: Filters) -> pd.DataFrame:
    where, params = f.where()
    return con.execute(
        f"""
        SELECT a.category, a.label, a.alert_type, count(*) AS count,
            sum(a.amount_at_stake) AS amount_at_stake
        FROM alerts AS a
        WHERE (a.uid IS NOT NULL AND a.uid IN (SELECT uid FROM contracts AS c WHERE {where}))
            OR (a.uid IS NULL AND a.buyer_siret IN (
                SELECT buyer_siret FROM contracts AS c WHERE {where}))
        GROUP BY ALL ORDER BY count DESC
        """,
        params + params,
    ).fetchdf()


def _key(alert: dict, name: str) -> str | None:
    """A key of an alert row; missing values come from pandas as None or NaN."""
    value = alert.get(name)
    return value if isinstance(value, str) and value else None


def alert_names(con: duckdb.DuckDBPyConnection, alert: dict) -> dict:
    """Readable names for the explanation of an alert."""
    names = {}
    buyer, holder, uid, division = (
        _key(alert, key) for key in ("buyer_siret", "holder_key", "uid", "cpv_division")
    )
    if buyer:
        row = con.execute("SELECT buyer_name FROM buyers WHERE buyer_siret = ?", [buyer]).fetchone()
        names["buyer_name"] = row[0] if row else None
    if holder:
        row = con.execute(
            "SELECT supplier_name FROM suppliers WHERE holder_key = ?", [holder]
        ).fetchone()
        names["supplier_name"] = row[0] if row else None
    if uid:
        row = con.execute(
            """SELECT t.description, d.cpv_label FROM contracts AS c
               LEFT JOIN contract_descriptions AS t USING (uid)
               LEFT JOIN cpv AS d USING (cpv_code) WHERE c.uid = ?""",
            [uid],
        ).fetchone()
        if row:
            names["description"], names["cpv_label"] = row
    elif division:
        row = con.execute(
            "SELECT cpv_label FROM cpv WHERE cpv_code = ?", [division + "000000"]
        ).fetchone()
        names["cpv_label"] = row[0] if row else None
    return names


def buyer_search(con: duckdb.DuckDBPyConnection, text: str, limit: int = 20) -> pd.DataFrame:
    return con.execute(
        """
        SELECT b.buyer_siret, b.buyer_name, b.buyer_department, count(*) AS contracts
        FROM buyers AS b JOIN contracts AS c USING (buyer_siret)
        WHERE b.buyer_name ILIKE ? OR b.buyer_siret = ?
        GROUP BY ALL ORDER BY contracts DESC
        LIMIT ?
        """,
        [f"%{text}%", text.strip(), limit],
    ).fetchdf()


def category_markets(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    return con.execute("SELECT * FROM category_markets ORDER BY total_value DESC").fetchdf()


def segments(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    return con.execute(
        """
        SELECT segment, count(*) AS suppliers, median(contracts) AS median_contracts,
            median(buyers) AS median_buyers, median(total_value) AS median_value,
            median(top_buyer_share) AS median_top_buyer_share,
            median(median_distance_km) AS median_distance_km
        FROM supplier_profiles GROUP BY segment ORDER BY suppliers DESC
        """
    ).fetchdf()


def evidence_dict(evidence: str | dict) -> dict:
    return json.loads(evidence) if isinstance(evidence, str) else evidence
