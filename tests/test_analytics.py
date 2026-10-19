"""Filters become bound SQL parameters, never SQL text."""

from __future__ import annotations

from pathlib import Path

import pytest

from procurement_intel import analytics
from procurement_intel.config import Settings

MARTS = Settings.from_env().marts_dir


def test_default_filters_are_national():
    filters = analytics.Filters()
    where, params = filters.where()
    assert where == "c.notification_year BETWEEN ? AND ?"
    assert params == [2019, 2026]
    assert filters.is_national


def test_filter_values_are_parameters():
    hostile = "x'); DROP TABLE contracts; --"
    filters = analytics.Filters(
        first_year=2022,
        regions=("84", "11"),
        buyer_categories=(hostile,),
        contract_types=("Works",),
        buyer_siret="21690123100011",
        include_frameworks=False,
    )
    where, params = filters.where("x")
    assert hostile not in where
    assert hostile in params
    assert "x.buyer_region_code IN (?, ?)" in where
    assert "NOT x.is_framework" in where
    assert params == [2022, 2026, "84", "11", hostile, "Works", "21690123100011"]
    assert not filters.is_national


def test_evidence_dict_accepts_json_and_dicts():
    assert analytics.evidence_dict('{"a": 1}') == {"a": 1}
    assert analytics.evidence_dict({"a": 1}) == {"a": 1}


needs_marts = pytest.mark.skipif(
    not (MARTS / "contracts").exists(), reason="the marts are not built"
)


@needs_marts
def test_overview_on_the_marts():
    con = analytics.connect(Path(MARTS))
    data = analytics.overview(con, analytics.Filters(first_year=2024, last_year=2024))
    assert data.kpis["contracts"] > 0
    assert 0 < data.kpis["single_bid_rate"] < 1
    assert data.by_year["year"].tolist() == [2024]
    assert abs(data.by_division["share"].sum() - 1) < 1e-6
    assert set(data.single_bid_by_year.columns) == {
        "year",
        "single_bid_rate",
        "procedures",
        "reporting_share",
    }
    concentration = analytics.supplier_concentration(con, analytics.Filters())
    assert 0 < concentration["hhi"] <= concentration["top10_share"] <= 1


@needs_marts
def test_alert_queue_is_ordered_by_priority():
    con = analytics.connect(Path(MARTS))
    frame = analytics.alerts(con, analytics.Filters(), limit=50)
    assert len(frame) == 50
    assert frame["priority"].is_monotonic_decreasing


@needs_marts
def test_names_of_contract_and_buyer_level_alerts():
    con = analytics.connect(Path(MARTS))
    frame = analytics.alerts(con, analytics.Filters(), types=("buyer_single_bid_rate",), limit=3)
    buyer_level = frame.iloc[0].to_dict()  # no contract, no supplier: NaN in pandas
    assert set(analytics.alert_names(con, buyer_level)) == {"buyer_name"}
    frame = analytics.alerts(con, analytics.Filters(), types=("single_bid",), limit=3)
    names = analytics.alert_names(con, frame.iloc[0].to_dict())
    assert {"buyer_name", "supplier_name", "cpv_label"} <= set(names)
