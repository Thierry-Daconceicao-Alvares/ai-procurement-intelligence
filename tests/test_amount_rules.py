"""Rule-based amount checks of 04_amount_quality.sql."""

from __future__ import annotations

from datetime import date

import pytest
from conftest import contract, load_contracts, run_sql


def issues(con, rows) -> dict[str, str | None]:
    load_contracts(con, rows)
    con.execute(
        "CREATE TABLE int_duplicates AS SELECT NULL::VARCHAR AS uid, true AS is_canonical "
        "WHERE false"
    )
    run_sql(con, "04_amount_quality")
    return dict(con.execute("SELECT uid, amount_issue FROM int_amount_quality").fetchall())


@pytest.mark.parametrize(
    ("amount", "expected"),
    [
        (999_999_999.0, "placeholder"),
        (99_999_999.99, "placeholder"),
        (399_999_996.0, "placeholder"),  # four lots of 99,999,999
        (99_999_999_999.0, "placeholder"),
        (50_000_000.0, None),
        (12_345_678.0, None),
        (9_999_999.0, None),  # below 10 million euros: not considered
        (50.0, "out_of_bounds"),
        (20_000_000_000.0, "out_of_bounds"),
        (250_000.0, None),
    ],
)
def test_placeholders_and_bounds(con, amount, expected):
    assert issues(con, [contract("a", initial_amount=amount)])["a"] == expected


def test_missing_amount_has_no_issue(con):
    assert issues(con, [contract("a", initial_amount=None)])["a"] is None


def test_default_ceiling_reused_for_unrelated_purchases(con):
    typical = [
        contract(
            f"t{i}", initial_amount=80_000.0 + 10_000 * i, notification_date=date(2023, 1, 1 + i)
        )
        for i in range(10)
    ]
    ceilings = [
        contract(f"c{i}", initial_amount=1e9, cpv_code=code, notification_date=date(2024, 1 + i, 1))
        for i, code in enumerate(
            ["15800000", "15800000", "39830000", "85310000", "03000000", "90910000"]
        )
    ]
    found = issues(con, typical + ceilings)
    assert all(found[f"c{i}"] == "default_ceiling" for i in range(6))
    assert all(found[f"t{i}"] is None for i in range(10))


def test_standard_ceilings_of_a_large_buyer_are_kept(con):
    large = [
        contract(f"l{i}", initial_amount=5e6 + 2.5e6 * i, notification_date=date(2023, 1, 1 + i))
        for i in range(20)
    ]
    ceilings = [
        contract(f"c{i}", initial_amount=2e8, cpv_code=code, notification_date=date(2024, 1 + i, 1))
        for i, code in enumerate(
            ["33100000", "33100000", "72000000", "48000000", "30200000", "64200000"]
        )
    ]
    found = issues(con, large + ceilings)
    assert all(value is None for value in found.values())


def test_same_amount_in_one_category_is_not_a_ceiling(con):
    typical = [contract(f"t{i}", initial_amount=90_000.0) for i in range(10)]
    repeated = [
        contract(f"r{i}", initial_amount=5e7, notification_date=date(2024, 1 + i, 1))
        for i in range(6)
    ]
    found = issues(con, typical + repeated)
    assert all(found[f"r{i}"] is None for i in range(6))
