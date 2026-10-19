"""The Power BI project refers only to tables, columns and measures that exist,
and its tables match the Parquet marts they load."""

from __future__ import annotations

import importlib.util
import re
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "powerbi"
DEFINITION = PROJECT / "ProcurementIntelligence.SemanticModel" / "definition"
MARTS = ROOT / "data" / "marts"


def load_validator():
    spec = importlib.util.spec_from_file_location(
        "powerbi_validate", ROOT / "scripts" / "powerbi" / "validate.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look their module up
    spec.loader.exec_module(module)
    return module


validate = load_validator()


def test_visuals_measures_and_relationships_refer_to_existing_objects():
    assert validate.check_references(PROJECT) == []


def test_every_table_is_loaded_and_related():
    model = validate.read_model(DEFINITION)
    assert set(model.tables) == {
        "Contracts",
        "ContractHolders",
        "Buyers",
        "Suppliers",
        "CPV",
        "Alerts",
        "CategoryMarkets",
        "Dates",
        "Measures",
    }
    related = {t for ft, _, tt, _ in model.relationships for t in (ft, tt)}
    assert related == set(model.tables) - {"Measures"}
    assert len(model.measures) >= 18
    assert set(model.measures.values()) == {"Measures"}


def test_reference_checks_find_errors(tmp_path):
    broken = tmp_path / "powerbi"
    shutil.copytree(PROJECT, broken)
    measures = broken / DEFINITION.relative_to(PROJECT) / "tables" / "Measures.tmdl"
    text = measures.read_text(encoding="utf-8")
    measures.write_text(
        text.replace("SUM ( Contracts[awarded_amount] )", "SUM ( Contracts[awarded] )"),
        encoding="utf-8",
    )
    errors = validate.check_references(broken)
    assert errors == ["[Awarded Value] uses the unknown column Contracts[awarded]"]


# Parquet source of each table, the columns its query drops and the columns
# it adds by merging other files.
SOURCES = {
    "Contracts": ("contracts", set(), set()),
    "ContractHolders": ("contract_holders", set(), set()),
    "Buyers": ("buyers.parquet", set(), set()),
    "Suppliers": (
        "suppliers.parquet",
        set(),
        {
            "segment",
            "profile_contracts",
            "profile_buyers",
            "profile_divisions",
            "top_buyer_share",
            "median_distance_km",
            "profile_single_bid_share",
            "profile_framework_share",
        },
    ),
    "CPV": ("cpv.parquet", set(), set()),
    "Alerts": ("alerts", {"evidence"}, {"buyer_name", "supplier_name"}),
    "CategoryMarkets": ("category_markets.parquet", set(), set()),
    "Dates": ("dates.parquet", set(), set()),
}
TYPES = {
    "string": "string",
    "large_string": "string",
    "int16": "int64",
    "int32": "int64",
    "int64": "int64",
    "double": "double",
    "date32[day]": "dateTime",
    "timestamp[us]": "dateTime",
    "bool": "boolean",
}


def tmdl_types(table: str) -> dict[str, str]:
    types, column = {}, None
    for line in (DEFINITION / "tables" / f"{table}.tmdl").read_text(encoding="utf-8").splitlines():
        if match := re.match(r"^\tcolumn (\S+)", line):
            column = match.group(1).strip("'")
        elif column and (match := re.match(r"^\t\tdataType: (\w+)", line)):
            types[column] = match.group(1)
    return types


@pytest.mark.skipif(not (MARTS / "contracts").exists(), reason="the marts are not built")
@pytest.mark.parametrize("table", sorted(SOURCES))
def test_tables_match_the_marts(table):
    import pyarrow.parquet as pq

    source, dropped, added = SOURCES[table]
    path = MARTS / source
    schema = pq.read_schema(next(path.glob("*.parquet")) if path.is_dir() else path)
    expected = {f.name: TYPES[str(f.type)] for f in schema if f.name not in dropped}
    declared = tmdl_types(table)
    assert set(declared) == set(expected) | added
    assert {c: declared[c] for c in expected} == expected
