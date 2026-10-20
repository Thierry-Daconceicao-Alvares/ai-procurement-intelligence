"""Export the marts to Parquet, for the application and Power BI.

The files in data/marts/ are the only data the application reads, and the
source tables of the Power BI semantic model. Large tables are split into
files of less than 30 MB (GitHub recommends files under 50 MB).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import duckdb

from .config import Settings

# Table -> SELECT exported. Columns are listed so that the files have a stable
# schema for Power BI.
EXPORTS = {
    "contracts": """
        SELECT
            i.contract_id, uid, contract_ref, buyer_siret, lead_holder_key, notification_date,
            notification_year, notification_month, contract_type, contract_nature,
            is_framework, procedure_type, procedure_group, offers_received, cpv_code,
            cpv_division, cpv_group, reported_amount, awarded_amount, amount_status,
            amount_issue, expected_amount, amount_z_model, duration_months, price_form,
            price_variation, modification_count, reported_current_amount, amount_change_ratio,
            amount_change_status, holder_count, buyer_category, buyer_department_code,
            buyer_region_code, records, record_group_kind, record_buyers, source_dataset
        FROM fct_contracts JOIN contract_ids AS i USING (uid)
        ORDER BY notification_date, uid
    """,
    # Descriptions take most of the space: they are kept for the contracts
    # that have an alert, where a reviewer reads them.
    "contract_descriptions": """
        SELECT uid, left(description, 300) AS description
        FROM fct_contracts
        WHERE uid IN (SELECT uid FROM fct_alerts) AND description IS NOT NULL
        ORDER BY uid
    """,
    "contract_holders": """
        SELECT i.contract_id, h.*
        FROM fct_contract_holders AS h JOIN contract_ids AS i USING (uid)
        ORDER BY uid, holder_key
    """,
    "buyers": "SELECT * FROM dim_buyers ORDER BY buyer_siret",
    "suppliers": "SELECT * FROM dim_suppliers ORDER BY holder_key",
    "cpv": "SELECT * FROM dim_cpv ORDER BY cpv_code",
    "alerts": """
        SELECT alert_id, alert_type, category, label, severity, priority, i.contract_id, uid,
            buyer_siret, holder_key, cpv_division, reference_date, amount_at_stake, title,
            recommended_action, reference, evidence
        FROM fct_alerts LEFT JOIN contract_ids AS i USING (uid)
        ORDER BY priority DESC, alert_id
    """,
    "supplier_profiles": "SELECT * FROM fct_supplier_profiles ORDER BY holder_key",
    "category_markets": "SELECT * FROM fct_category_markets ORDER BY cpv_group",
    "dates": """
        SELECT
            d::DATE AS date,
            year(d) AS year,
            quarter(d) AS quarter,
            month(d) AS month,
            strftime(d, '%Y-%m') AS year_month,
            strftime(d, '%b %Y') AS month_label
        FROM range(DATE '{start}', DATE '{end}' + INTERVAL 1 DAY, INTERVAL 1 DAY) AS t(d)
    """,
}

SPLIT = {"contracts", "contract_holders", "alerts"}


def export_marts(con: duckdb.DuckDBPyConnection, settings: Settings) -> dict:
    out = settings.marts_dir
    out.mkdir(parents=True, exist_ok=True)
    # An integer key per contract. A few identifiers differ only by case, and
    # Power BI compares text without case: it cannot relate tables on uid.
    con.execute(
        """CREATE OR REPLACE TEMP TABLE contract_ids AS
        SELECT uid, row_number() OVER (ORDER BY notification_date, uid) AS contract_id
        FROM fct_contracts"""
    )
    sizes = {}
    for name, query in EXPORTS.items():
        query = query.format(start=settings.start_date, end=settings.end_date)
        target = out / name
        if name in SPLIT:
            shutil.rmtree(target, ignore_errors=True)
            con.execute(
                f"""COPY ({query}) TO '{target}' (
                    FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000,
                    FILE_SIZE_BYTES '20MB', FILENAME_PATTERN 'part-{{i}}')"""
            )
            files = list(target.glob("*.parquet"))
        else:
            path = out / f"{name}.parquet"
            con.execute(f"COPY ({query}) TO '{path}' (FORMAT PARQUET, COMPRESSION ZSTD)")
            files = [path]
        sizes[name] = round(sum(f.stat().st_size for f in files) / 1e6, 1)
    (out / "README.md").write_text(MARTS_README, encoding="utf-8")
    return sizes


def data_quality_funnel(con: duckdb.DuckDBPyConnection) -> dict:
    """From raw rows to the contracts used in totals."""

    def one(sql: str):
        return con.execute(sql).fetchone()[0]

    def removed(kind: str) -> int:
        return one(
            f"SELECT count(*) FROM int_duplicates WHERE NOT is_canonical AND group_kind = '{kind}'"
        )

    def amounts(condition: str) -> int:
        return one(f"SELECT count(*) FROM fct_contracts WHERE {condition}")

    return {
        "raw_rows": one("SELECT count(*) FROM stg_decp"),
        "contracts_in_window": one("SELECT count(*) FROM int_contracts"),
        "duplicate_records_removed": removed("duplicate"),
        "framework_records_merged": removed("multi_supplier_framework"),
        "joint_purchase_records_merged": removed("joint_purchase"),
        "contracts_kept": one("SELECT count(*) FROM fct_contracts"),
        "amounts_missing": amounts("amount_status = 'missing'"),
        "amounts_implausible": amounts("amount_status = 'implausible'"),
        "amounts_implausible_by_issue": dict(
            con.execute(
                "SELECT amount_issue, count(*) FROM fct_contracts "
                "WHERE amount_status = 'implausible' GROUP BY 1 ORDER BY 2 DESC"
            ).fetchall()
        ),
        "amounts_valid": amounts("amount_status = 'valid'"),
        "reported_value_implausible_eur": one(
            "SELECT sum(reported_amount) FROM fct_contracts WHERE amount_status = 'implausible'"
        ),
        "reported_value_all_records_eur": one("SELECT sum(initial_amount) FROM int_contracts"),
        "awarded_value_valid_eur": one("SELECT sum(awarded_amount) FROM fct_contracts"),
        "awarded_value_excluding_frameworks_eur": one(
            "SELECT sum(awarded_amount) FROM fct_contracts WHERE NOT is_framework"
        ),
        "buyers": one("SELECT count(*) FROM dim_buyers"),
        "suppliers": one("SELECT count(*) FROM dim_suppliers"),
        "individual_entrepreneurs_masked": one(
            "SELECT count(*) FROM dim_suppliers WHERE is_individual_entrepreneur"
        ),
    }


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str) + "\n", encoding="utf-8")


MARTS_README = """# Marts

Generated by `python -m procurement_intel build` from the data snapshot. Amounts
are in euros excluding VAT, as awarded (the maximum amount for framework
agreements), not amounts paid. Only contracts with `amount_status = 'valid'`
have an `awarded_amount`; `amount_issue` gives the reason of an implausible
amount (placeholder, out_of_bounds, default_ceiling, far_from_expected).

`contract_id` is the integer key of a contract in `contracts/`,
`contract_holders/` and `alerts/`. Use it for joins in tools that compare text
without case, such as Power BI: a few `uid` differ only by case.

| File | Grain |
|---|---|
| `contracts/` | One contract (after merging the records of one award) |
| `contract_descriptions.parquet` | Description of the contracts that have an alert |
| `contract_holders/` | One holder of a contract, with its share of the amount |
| `buyers.parquet` | One public buyer (SIRET) |
| `suppliers.parquet` | One supplier (SIREN), names of individual entrepreneurs withheld |
| `cpv.parquet` | One CPV 2008 code with its division, group and class |
| `alerts/` | One alert to review, with its evidence (JSON) |
| `supplier_profiles.parquet` | One supplier with at least 3 contracts, with its segment |
| `category_markets.parquet` | One CPV group: concentration, competition, Kraljic quadrant |
| `dates.parquet` | Calendar of the analysis window |
"""
