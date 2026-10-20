"""Write the Power BI project (PBIP) in powerbi/ from the definitions below.

The project has two items, both plain text:
- ProcurementIntelligence.SemanticModel: the semantic model in TMDL. Its
  tables load the Parquet marts of data/marts/ with Power Query, from the
  folder given by the DataFolder parameter;
- ProcurementIntelligence.Report: a four-page report in the PBIR format.

Power BI Desktop rewrites these files when the project is saved. Run this
script again only to start over from the definitions below; edits made in
Desktop would be lost.

Usage: python scripts/powerbi/build_pbip.py [--out powerbi]
"""

from __future__ import annotations

import argparse
import json
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NAME = "ProcurementIntelligence"
NAMESPACE = uuid.UUID("6f1d3c52-8a0e-4c39-9d2b-3f7a51c0e2b4")
SCHEMAS = "https://developer.microsoft.com/json-schemas/fabric"
VISUAL_SCHEMA = f"{SCHEMAS}/item/report/definition/visualContainer/2.2.0/schema.json"
PAGE_SCHEMA = f"{SCHEMAS}/item/report/definition/page/2.0.0/schema.json"
REPORT_SCHEMA = f"{SCHEMAS}/item/report/definition/report/3.0.0/schema.json"
THEME_FILE = "ProcurementIntelligence.json"
BASE_THEME = "CY24SU10"
DEFAULT_FOLDER = r"C:\ai-procurement-intelligence\data\marts"
# "Measures" is a reserved table name: Power BI Desktop refuses to open a model
# that uses it ("Unsupported Table name").
MEASURES_TABLE = "_Measures"
SOURCE_NOTE = (
    "Source: DECP consolidated by decp.info (Licence Ouverte 2.0) and Base Sirene (INSEE). "
    "Amounts excluding VAT; alerts are risk indicators, not findings."
)

# Palette of the Streamlit application: categorical slots in a fixed order.
PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
INK, INK_SECONDARY = "#0b0b0b", "#52514e"


def tag(*parts: str) -> str:
    """Stable lineage tag: the same object always gets the same identifier."""
    return str(uuid.uuid5(NAMESPACE, "/".join(parts)))


def quote(name: str) -> str:
    """TMDL name: quoted when it is not a simple identifier."""
    if name.replace("_", "a").isalnum() and not name[0].isdigit():
        return name
    return "'" + name.replace("'", "''") + "'"


# ---------------------------------------------------------------- semantic model


@dataclass
class Column:
    name: str
    data_type: str  # string, int64, double, dateTime, boolean
    format: str | None = None
    summarize: str = "none"
    hidden: bool = False
    key: bool = False
    sort_by: str | None = None
    description: str | None = None


@dataclass
class Table:
    name: str
    description: str
    columns: list[Column]
    source: str  # Power Query M
    date_table: bool = False


@dataclass
class Measure:
    name: str
    expression: str
    format: str
    folder: str
    description: str


def text(name: str, description: str | None = None, hidden: bool = False) -> Column:
    return Column(name, "string", hidden=hidden, description=description)


def integer(name: str, description: str | None = None, summarize: str = "none") -> Column:
    return Column(
        name,
        "int64",
        format="0" if summarize == "none" else "#,0",
        summarize=summarize,
        description=description,
    )


def amount(name: str, description: str | None = None, summarize: str = "sum") -> Column:
    return Column(name, "double", format="€#,0", summarize=summarize, description=description)


def number(name: str, fmt: str = "#,0.00", description: str | None = None) -> Column:
    return Column(name, "double", format=fmt, description=description)


def share(name: str, description: str | None = None) -> Column:
    return Column(name, "double", format="0.0%", description=description)


def day(name: str, description: str | None = None) -> Column:
    return Column(name, "dateTime", format="yyyy-mm-dd", description=description)


def flag(name: str, description: str | None = None) -> Column:
    return Column(name, "boolean", format='"TRUE";"TRUE";"FALSE"', description=description)


ROOT_M = 'Root = Text.TrimEnd(DataFolder, "\\") & "\\"'


def m_file(file: str, *steps: str) -> str:
    """M query reading one Parquet file of the marts folder."""
    lines = [ROOT_M, f'Source = Parquet.Document(File.Contents(Root & "{file}"))', *steps]
    return _m(lines)


def m_folder(folder: str, *steps: str) -> str:
    """M query reading the Parquet parts of one folder of the marts."""
    lines = [
        ROOT_M,
        f'Files = Folder.Files(Root & "{folder}")',
        'Parts = Table.SelectRows(Files, each Text.Lower([Extension]) = ".parquet")',
        "Source = Table.Combine(List.Transform(Parts[Content], each Parquet.Document(_)))",
        *steps,
    ]
    return _m(lines)


def _m(steps: list[str]) -> str:
    """let ... in <last step>: every step is `Name = expression`."""
    body = ",\n".join(f"    {step}" for step in steps)
    last = steps[-1].split("=", 1)[0].strip()
    return f"let\n{body}\nin\n    {last}"


# One folder data source for the queries that combine several files, so that
# Power Query does not ask for privacy levels.
IN_ROOT = [
    ROOT_M,
    "Files = Folder.Files(Root)",
    "InRoot = Table.SelectRows(Files, each Text.Lower([Folder Path]) = Text.Lower(Root))",
]

TABLES = [
    Table(
        "Contracts",
        "One contract notified from January 2019 to September 2026, after merging the "
        "records of one award. awarded_amount is empty when the reported amount is missing "
        "or implausible.",
        [
            text("uid", "Identifier of the contract in the consolidated data."),
            text("contract_ref", "Reference of the contract given by the buyer."),
            text("buyer_siret", "SIRET of the buyer."),
            text("lead_holder_key", "First holder of the contract (FR:<SIREN> or ID:<id>)."),
            day("notification_date", "Date the contract was notified to its holder."),
            integer("notification_year"),
            Column("notification_month", "dateTime", format="mmm yyyy"),
            text("contract_type", "Works, Services or Supplies."),
            text("contract_nature"),
            flag("is_framework", "Framework agreement: the amount is a maximum."),
            text("procedure_type"),
            text("procedure_group", "Competitive, Non-competitive or Unknown."),
            integer("offers_received", "Number of offers received, when reported."),
            text("cpv_code", "CPV 2008 code chosen by the buyer."),
            text("cpv_division"),
            text("cpv_group"),
            amount("reported_amount", "Amount as published, before quality checks."),
            amount("awarded_amount", "Reported amount when valid; empty otherwise."),
            text("amount_status", "valid, implausible or missing."),
            text(
                "amount_issue",
                "Why an amount is implausible: placeholder, out_of_bounds, "
                "default_ceiling or far_from_expected.",
            ),
            amount(
                "expected_amount",
                "Amount expected for comparable contracts (model).",
                summarize="none",
            ),
            number("amount_z_model", "0.00", "Robust z-score of the amount against the model."),
            integer("duration_months"),
            text("price_form"),
            text("price_variation"),
            integer("modification_count", summarize="sum"),
            amount(
                "reported_current_amount",
                "Amount after modifications, as published.",
                summarize="none",
            ),
            share("amount_change_ratio", "Change of amount through modifications."),
            text("amount_change_status"),
            integer("holder_count"),
            text("buyer_category"),
            text("buyer_department_code"),
            text("buyer_region_code"),
            integer("records", "Records merged into this contract."),
            text("record_group_kind"),
            integer("record_buyers", "Buyers that published this contract (joint purchase)."),
            text("source_dataset", "Platform or flow that published the record."),
        ],
        m_folder("contracts"),
    ),
    Table(
        "ContractHolders",
        "One holder of a contract. The awarded amount is shared equally between the holders "
        "of a contract, so that supplier totals add up to the contract total.",
        [
            text("uid"),
            text("holder_key"),
            amount("allocated_amount", "Share of the awarded amount of the contract."),
            text("holder_size", "SME, Mid-sized company or Large company."),
            text("holder_naf_code"),
            text("holder_department_code"),
            text("holder_region_code"),
            integer("holder_distance_km", "Distance between the holder and the buyer."),
        ],
        m_folder("contract_holders"),
    ),
    Table(
        "Buyers",
        "One public buyer (SIRET).",
        [
            text("buyer_siret"),
            text("buyer_name"),
            text("buyer_category"),
            text("buyer_department_code"),
            text("buyer_department"),
            text("buyer_region_code"),
            text("buyer_region"),
        ],
        m_file("buyers.parquet", "Kept = Table.SelectRows(Source, each [buyer_siret] <> null)"),
    ),
    Table(
        "Suppliers",
        "One supplier (SIREN). Names of individual entrepreneurs are withheld. Suppliers "
        "with at least three contracts have a segment and profile metrics.",
        [
            text("holder_key"),
            text("holder_siren"),
            flag("is_individual_entrepreneur"),
            text("supplier_name"),
            text("holder_size"),
            text("naf_code"),
            text("naf_label", "Main activity (NAF), French label."),
            text("department_code"),
            text("region_code"),
            integer("legal_category_code"),
            day("creation_date"),
            flag("is_ceased"),
            day("cessation_date"),
            text("headcount_band"),
            flag("is_social_economy"),
            flag("found_in_sirene"),
            text("segment", "Segment of the supplier (k-means on its public business)."),
            integer("profile_contracts"),
            integer("profile_buyers"),
            integer("profile_divisions"),
            share("top_buyer_share", "Share of the supplier's value from its main buyer."),
            number("median_distance_km", "#,0"),
            share("profile_single_bid_share"),
            share("profile_framework_share"),
        ],
        _m(
            [
                *IN_ROOT,
                'Suppliers = Parquet.Document(InRoot{[Name = "suppliers.parquet"]}[Content])',
                'Profiles = Parquet.Document(InRoot{[Name = "supplier_profiles.parquet"]}'
                "[Content])",
                'Joined = Table.NestedJoin(Suppliers, {"holder_key"}, Profiles, '
                '{"holder_key"}, "Profile", JoinKind.LeftOuter)',
                'Expanded = Table.ExpandTableColumn(Joined, "Profile", {"segment", '
                '"contracts", "buyers", "divisions", "top_buyer_share", "median_distance_km", '
                '"single_bid_share", "framework_share"}, {"segment", "profile_contracts", '
                '"profile_buyers", "profile_divisions", "top_buyer_share", '
                '"median_distance_km", "profile_single_bid_share", "profile_framework_share"})',
                'Typed = Table.TransformColumnTypes(Expanded, {{"segment", type text}, '
                '{"profile_contracts", Int64.Type}, {"profile_buyers", Int64.Type}, '
                '{"profile_divisions", Int64.Type}, {"top_buyer_share", type number}, '
                '{"median_distance_km", type number}, {"profile_single_bid_share", '
                'type number}, {"profile_framework_share", type number}})',
            ]
        ),
    ),
    Table(
        "CPV",
        "Common Procurement Vocabulary (CPV 2008): every code with its division, group and class.",
        [
            text("cpv_code"),
            text("cpv_label"),
            text("cpv_label_fr"),
            text("cpv_level"),
            text("cpv_division"),
            text("cpv_division_label"),
            text("cpv_group"),
            text("cpv_group_label"),
            text("cpv_class"),
            text("cpv_class_label"),
        ],
        m_file("cpv.parquet"),
    ),
    Table(
        "Alerts",
        "One alert to review: a risk indicator computed from the published data, with its "
        "priority, title and recommended action. The evidence (JSON) stays in the marts.",
        [
            text("alert_id"),
            text("alert_type"),
            text("category"),
            text("label"),
            text("severity", "high, medium or low."),
            number(
                "priority",
                "0.0",
                "Severity points plus the order of magnitude of the amount at stake.",
            ),
            text("uid"),
            text("buyer_siret"),
            text("holder_key"),
            text("cpv_division"),
            Column("reference_date", "dateTime", format="yyyy-mm-dd"),
            amount("amount_at_stake", "Amount the alert is about; not a loss."),
            text("title"),
            text("recommended_action"),
            text("reference"),
            text("buyer_name"),
            text("supplier_name"),
        ],
        _m(
            [
                *IN_ROOT,
                "AlertFiles = Table.SelectRows(Files, each Text.Lower([Folder Path]) = "
                'Text.Lower(Root & "alerts\\") and Text.Lower([Extension]) = ".parquet")',
                "Alerts = Table.Combine(List.Transform(AlertFiles[Content], "
                "each Parquet.Document(_)))",
                'WithoutEvidence = Table.RemoveColumns(Alerts, {"evidence"})',
                "Buyers = Table.SelectRows(Table.SelectColumns(Parquet.Document(InRoot{[Name "
                '= "buyers.parquet"]}[Content]), {"buyer_siret", "buyer_name"}), '
                "each [buyer_siret] <> null)",
                "Suppliers = Table.SelectColumns(Parquet.Document(InRoot{[Name = "
                '"suppliers.parquet"]}[Content]), {"holder_key", "supplier_name"})',
                'WithBuyer = Table.NestedJoin(WithoutEvidence, {"buyer_siret"}, Buyers, '
                '{"buyer_siret"}, "Buyer", JoinKind.LeftOuter)',
                'BuyerName = Table.ExpandTableColumn(WithBuyer, "Buyer", {"buyer_name"})',
                'WithSupplier = Table.NestedJoin(BuyerName, {"holder_key"}, Suppliers, '
                '{"holder_key"}, "Supplier", JoinKind.LeftOuter)',
                'SupplierName = Table.ExpandTableColumn(WithSupplier, "Supplier", '
                '{"supplier_name"})',
                'Typed = Table.TransformColumnTypes(SupplierName, {{"buyer_name", type text}, '
                '{"supplier_name", type text}})',
            ]
        ),
    ),
    Table(
        "CategoryMarkets",
        "One CPV group with at least 30 competitive procedures reporting their offers: "
        "national concentration, competition and Kraljic quadrant.",
        [
            text("cpv_group"),
            text("cpv_group_label"),
            text("cpv_division"),
            integer("contracts", summarize="sum"),
            amount("total_value"),
            integer("suppliers", summarize="sum"),
            number("hhi", "0.000", "Herfindahl-Hirschman index of the group (0 to 1)."),
            share("top_supplier_share"),
            number("avg_offers", "0.0"),
            share("single_bid_share"),
            integer("procedures_with_offers", summarize="sum"),
            number(
                "supply_risk",
                "0.00",
                "Mean percentile of HHI, single-offer share and scarcity of offers.",
            ),
            number("value_percentile", "0.00", "Value of the group as a percentile."),
            text("kraljic_quadrant", "Strategic, Bottleneck, Leverage or Non-critical."),
        ],
        m_file("category_markets.parquet"),
    ),
    Table(
        "Dates",
        "Calendar of the analysis window (1 January 2019 to 30 September 2026).",
        [
            Column("date", "dateTime", format="yyyy-mm-dd", key=True),
            integer("year"),
            integer("quarter"),
            integer("month"),
            Column("year_month", "string", hidden=True),
            Column("month_label", "string", sort_by="year_month"),
        ],
        m_file("dates.parquet"),
        date_table=True,
    ),
]

# Contracts, buyers and suppliers are filtered through these relationships:
# Dates, Buyers and CPV filter Contracts, which filters ContractHolders and
# Alerts; Suppliers filters ContractHolders; CategoryMarkets filters CPV.
RELATIONSHIPS = [
    ("Contracts", "buyer_siret", "Buyers", "buyer_siret"),
    ("Contracts", "cpv_code", "CPV", "cpv_code"),
    ("Contracts", "notification_date", "Dates", "date"),
    ("ContractHolders", "uid", "Contracts", "uid"),
    ("ContractHolders", "holder_key", "Suppliers", "holder_key"),
    ("Alerts", "uid", "Contracts", "uid"),
    ("CPV", "cpv_group", "CategoryMarkets", "cpv_group"),
]

COMPETITIVE = 'KEEPFILTERS ( Contracts[procedure_group] = "Competitive" )'
REPORTED = "KEEPFILTERS ( NOT ISBLANK ( Contracts[offers_received] ) )"

MEASURES = [
    Measure(
        "Awarded Value",
        "SUM ( Contracts[awarded_amount] )",
        "€#,0",
        "Spend",
        "Sum of the valid awarded amounts, excluding VAT (framework agreements count for "
        "their maximum).",
    ),
    Measure(
        "Contract Value",
        "CALCULATE ( [Awarded Value], KEEPFILTERS ( Contracts[is_framework] = FALSE () ) )",
        "€#,0",
        "Spend",
        "Awarded value of the contracts that are not framework agreements.",
    ),
    Measure(
        "Framework Value",
        "CALCULATE ( [Awarded Value], KEEPFILTERS ( Contracts[is_framework] = TRUE () ) )",
        "€#,0",
        "Spend",
        "Maximum amount of the framework agreements.",
    ),
    Measure(
        "Contracts",
        "COUNTROWS ( Contracts )",
        "#,0",
        "Spend",
        "Contracts, after merging the records of one award.",
    ),
    Measure(
        "Buyers",
        "DISTINCTCOUNT ( Contracts[buyer_siret] )",
        "#,0",
        "Spend",
        "Public buyers with at least one contract.",
    ),
    Measure(
        "Awarded Value YoY %",
        "VAR CurrentValue = [Awarded Value]\n"
        "VAR PreviousValue =\n"
        "    CALCULATE ( [Awarded Value], DATEADD ( Dates[date], -1, YEAR ) )\n"
        "RETURN\n"
        "    IF (\n"
        "        NOT ISBLANK ( PreviousValue ),\n"
        "        DIVIDE ( CurrentValue - PreviousValue, PreviousValue )\n"
        "    )",
        "0.0%",
        "Spend",
        "Change of the awarded value over the same period one year earlier.",
    ),
    Measure(
        "Implausible Amounts",
        'CALCULATE ( [Contracts], KEEPFILTERS ( Contracts[amount_status] = "implausible" ) )',
        "#,0",
        "Data quality",
        "Contracts whose reported amount is implausible and excluded from totals.",
    ),
    Measure(
        "Single-offer Rate",
        "VAR Reported =\n"
        f"    CALCULATE ( COUNTROWS ( Contracts ), {COMPETITIVE}, {REPORTED} )\n"
        "VAR SingleOffer =\n"
        f"    CALCULATE ( COUNTROWS ( Contracts ), {COMPETITIVE}, "
        "KEEPFILTERS ( Contracts[offers_received] = 1 ) )\n"
        "RETURN\n"
        "    DIVIDE ( SingleOffer, Reported )",
        "0.0%",
        "Competition",
        "Share of competitive procedures that received a single offer, among those that "
        "report their number of offers.",
    ),
    Measure(
        "Offer Reporting Share",
        "VAR Competitive =\n"
        f"    CALCULATE ( COUNTROWS ( Contracts ), {COMPETITIVE} )\n"
        "VAR Reported =\n"
        f"    CALCULATE ( COUNTROWS ( Contracts ), {COMPETITIVE}, {REPORTED} )\n"
        "RETURN\n"
        "    DIVIDE ( Reported, Competitive )",
        "0.0%",
        "Competition",
        "Share of competitive procedures that report their number of offers.",
    ),
    Measure(
        "Single-offer Rate (reported years)",
        "IF ( [Offer Reporting Share] >= 0.5, [Single-offer Rate] )",
        "0.0%",
        "Competition",
        "Single-offer rate, shown only where at least half of the competitive procedures "
        "report their number of offers.",
    ),
    Measure(
        "Non-competitive Share",
        "DIVIDE (\n"
        "    CALCULATE (\n"
        "        COUNTROWS ( Contracts ),\n"
        '        KEEPFILTERS ( Contracts[procedure_group] = "Non-competitive" )\n'
        "    ),\n"
        "    COUNTROWS ( Contracts )\n"
        ")",
        "0.0%",
        "Competition",
        "Share of contracts awarded without publication or competition.",
    ),
    Measure(
        "Supplier Value",
        "SUM ( ContractHolders[allocated_amount] )",
        "€#,0",
        "Suppliers",
        "Awarded value allocated to the suppliers (shared equally between co-holders).",
    ),
    Measure(
        "Suppliers",
        "DISTINCTCOUNT ( ContractHolders[holder_key] )",
        "#,0",
        "Suppliers",
        "Suppliers holding at least one contract.",
    ),
    Measure(
        "Supplier Contracts",
        "DISTINCTCOUNT ( ContractHolders[uid] )",
        "#,0",
        "Suppliers",
        "Contracts held by the suppliers in the filter context.",
    ),
    Measure(
        "SME Share of Value",
        "DIVIDE (\n"
        "    CALCULATE (\n"
        "        [Supplier Value],\n"
        '        KEEPFILTERS ( ContractHolders[holder_size] = "SME" )\n'
        "    ),\n"
        "    [Supplier Value]\n"
        ")",
        "0.0%",
        "Suppliers",
        "Share of the supplier value held by small and medium-sized companies.",
    ),
    Measure(
        "Top 10 Supplier Share",
        "VAR Total = [Supplier Value]\n"
        "VAR TopTen =\n"
        "    SUMX (\n"
        "        TOPN ( 10, VALUES ( ContractHolders[holder_key] ), [Supplier Value], DESC ),\n"
        "        [Supplier Value]\n"
        "    )\n"
        "RETURN\n"
        "    DIVIDE ( TopTen, Total )",
        "0.0%",
        "Suppliers",
        "Share of the supplier value held by the 10 largest suppliers.",
    ),
    Measure(
        "Supplier HHI",
        "VAR Total = [Supplier Value]\n"
        "RETURN\n"
        "    IF (\n"
        "        Total > 0,\n"
        "        SUMX (\n"
        "            VALUES ( ContractHolders[holder_key] ),\n"
        "            DIVIDE ( [Supplier Value], Total ) ^ 2\n"
        "        ) * 10000\n"
        "    )",
        "#,0",
        "Suppliers",
        "Herfindahl-Hirschman index of the suppliers, from near 0 (many small suppliers) "
        "to 10,000 (a single supplier).",
    ),
    Measure(
        "Profiled Suppliers",
        "CALCULATE ( [Suppliers], KEEPFILTERS ( NOT ISBLANK ( Suppliers[segment] ) ) )",
        "#,0",
        "Suppliers",
        "Suppliers that have a segment (at least three contracts).",
    ),
    Measure("Alerts", "COUNTROWS ( Alerts )", "#,0", "Alerts", "Alerts to review."),
    Measure(
        "High-severity Alerts",
        'CALCULATE ( [Alerts], KEEPFILTERS ( Alerts[severity] = "high" ) )',
        "#,0",
        "Alerts",
        "Alerts of high severity.",
    ),
    Measure(
        "Amount at Stake",
        "SUM ( Alerts[amount_at_stake] )",
        "€#,0",
        "Alerts",
        "Sum of the amounts the alerts are about (awarded amount, increase, reporting "
        "error...): not a loss.",
    ),
    Measure(
        "Market Value",
        "SUM ( CategoryMarkets[total_value] )",
        "€#,0",
        "Markets",
        "Awarded value of the CPV groups (national markets).",
    ),
    Measure(
        "Market Value Percentile",
        "AVERAGE ( CategoryMarkets[value_percentile] )",
        "0.00",
        "Markets",
        "Value of the CPV group as a percentile among groups.",
    ),
    Measure(
        "Market Supply Risk",
        "AVERAGE ( CategoryMarkets[supply_risk] )",
        "0.00",
        "Markets",
        "Supply risk of the CPV group (0 to 1).",
    ),
]


def render_description(text_: str | None, indent: str) -> list[str]:
    return [f"{indent}/// {text_}"] if text_ else []


def render_table(table: Table) -> str:
    out = render_description(table.description, "")
    out += [f"table {quote(table.name)}"]
    if table.date_table:
        out += ["\tdataCategory: Time"]
    out += [f"\tlineageTag: {tag('table', table.name)}", ""]
    for column in table.columns:
        out += render_description(column.description, "\t")
        out += [f"\tcolumn {quote(column.name)}", f"\t\tdataType: {column.data_type}"]
        if column.hidden:
            out += ["\t\tisHidden"]
        if column.key:
            out += ["\t\tisKey"]
        if column.format:
            out += [f"\t\tformatString: {tmdl_text(column.format)}"]
        out += [
            f"\t\tlineageTag: {tag('column', table.name, column.name)}",
            f"\t\tsummarizeBy: {column.summarize}",
            f"\t\tsourceColumn: {column.name}",
        ]
        if column.sort_by:
            out += [f"\t\tsortByColumn: {quote(column.sort_by)}"]
        out += ["", "\t\tannotation SummarizationSetBy = Automatic", ""]
        if column.data_type == "dateTime":
            out += ["\t\tannotation UnderlyingDateTimeDataType = Date", ""]
    out += [f"\tpartition {quote(table.name)} = m", "\t\tmode: import", "\t\tsource ="]
    out += ["\t\t\t\t" + line for line in table.source.splitlines()]
    out += ["", "\tannotation PBI_ResultType = Table", ""]
    return "\n".join(out)


def tmdl_text(value: str) -> str:
    """A text property value: quoted, with its quotes doubled, when it contains
    a quote or starts or ends with a space (as Power BI Desktop writes it)."""
    if '"' in value or value != value.strip():
        return '"' + value.replace('"', '""') + '"'
    return value


def render_measures_table() -> str:
    out = [
        "/// Measures of the report, grouped in display folders.",
        f"table {MEASURES_TABLE}",
        f"\tlineageTag: {tag('table', MEASURES_TABLE)}",
        "",
    ]
    for measure in MEASURES:
        out += render_description(measure.description, "\t")
        lines = measure.expression.splitlines()
        if len(lines) == 1:
            out += [f"\tmeasure {quote(measure.name)} = {lines[0]}"]
        else:
            out += [f"\tmeasure {quote(measure.name)} ="]
            out += ["\t\t\t" + line for line in lines]
        out += [
            f"\t\tformatString: {tmdl_text(measure.format)}",
            f"\t\tdisplayFolder: {measure.folder}",
            f"\t\tlineageTag: {tag('measure', measure.name)}",
            "",
        ]
    out += [
        "\tcolumn Value",
        "\t\tisHidden",
        f"\t\tlineageTag: {tag('column', MEASURES_TABLE, 'Value')}",
        "\t\tsummarizeBy: none",
        "\t\tisNameInferred",
        "\t\tsourceColumn: [Value]",
        "",
        "\t\tannotation SummarizationSetBy = Automatic",
        "",
        f"\tpartition {MEASURES_TABLE} = calculated",
        "\t\tmode: import",
        '\t\tsource = ROW ( "Value", BLANK () )',
        "",
    ]
    return "\n".join(out)


def write_semantic_model(out: Path) -> None:
    item = out / f"{NAME}.SemanticModel"
    definition = item / "definition"
    (definition / "tables").mkdir(parents=True)
    write_json(
        item / "definition.pbism",
        {
            "$schema": f"{SCHEMAS}/item/semanticModel/definitionProperties/1.0.0/schema.json",
            "version": "4.0",
            "settings": {},
        },
    )
    write_text(definition / "database.tmdl", "database\n\tcompatibilityLevel: 1567\n")
    names = [t.name for t in TABLES] + [MEASURES_TABLE]
    query_order = json.dumps(["DataFolder", *[t.name for t in TABLES]])
    model = [
        "model Model",
        "\tculture: en-US",
        "\tdefaultPowerBIDataSourceVersion: powerBI_V3",
        "\tsourceQueryCulture: en-US",
        "\tdataAccessOptions",
        "\t\tlegacyRedirects",
        "\t\treturnErrorValuesAsNull",
        "",
        f"annotation PBI_QueryOrder = {query_order}",
        "",
        "annotation __PBI_TimeIntelligenceEnabled = 0",
        "",
        'annotation PBI_ProTooling = ["DevMode"]',
        "",
        *[f"ref table {quote(name)}" for name in names],
        "",
    ]
    write_text(definition / "model.tmdl", "\n".join(model))
    write_text(
        definition / "expressions.tmdl",
        "\n".join(
            [
                "/// Folder of the Parquet marts: data/marts in a clone of the repository.",
                f'expression DataFolder = "{DEFAULT_FOLDER}" meta [IsParameterQuery=true, '
                'Type="Text", IsParameterQueryRequired=true]',
                f"\tlineageTag: {tag('expression', 'DataFolder')}",
                "",
                "\tannotation PBI_ResultType = Text",
                "",
            ]
        ),
    )
    relationships = []
    for from_table, from_column, to_table, to_column in RELATIONSHIPS:
        relationships += [
            f"relationship {tag('relationship', from_table, from_column, to_table)}",
            f"\tfromColumn: {quote(from_table)}.{quote(from_column)}",
            f"\ttoColumn: {quote(to_table)}.{quote(to_column)}",
            "",
        ]
    write_text(definition / "relationships.tmdl", "\n".join(relationships))
    for table in TABLES:
        write_text(definition / "tables" / f"{table.name}.tmdl", render_table(table))
    write_text(definition / "tables" / f"{MEASURES_TABLE}.tmdl", render_measures_table())


# ------------------------------------------------------------------------ report


def literal(value: object) -> dict:
    """A literal of a formatting property: strings in single quotes, numbers
    with the D (double) suffix, booleans in lower case."""
    if isinstance(value, bool):
        text_ = "true" if value else "false"
    elif isinstance(value, int | float):
        text_ = f"{value}D"
    else:
        text_ = "'" + str(value).replace("'", "''") + "'"
    return {"expr": {"Literal": {"Value": text_}}}


def column(entity: str, prop: str) -> dict:
    return {"Column": {"Expression": {"SourceRef": {"Entity": entity}}, "Property": prop}}


def measure(prop: str) -> dict:
    return {"Measure": {"Expression": {"SourceRef": {"Entity": MEASURES_TABLE}}, "Property": prop}}


def projection(field_: dict, display: str | None = None, active: bool | None = None) -> dict:
    kind = "Column" if "Column" in field_ else "Measure"
    entity = field_[kind]["Expression"]["SourceRef"]["Entity"]
    prop = field_[kind]["Property"]
    item: dict = {"field": field_, "queryRef": f"{entity}.{prop}", "nativeQueryRef": prop}
    if display:
        item["displayName"] = display
    if active is not None:
        item["active"] = active
    return item


@dataclass
class Visual:
    name: str
    visual_type: str
    x: float
    y: float
    width: float
    height: float
    roles: dict[str, list[dict]] = field(default_factory=dict)
    sort: list[tuple[dict, str]] = field(default_factory=list)
    title: str | None = None
    objects: dict = field(default_factory=dict)
    sync_group: str | None = None

    def to_json(self, z: int) -> dict:
        visual: dict = {"visualType": self.visual_type}
        if self.roles:
            query: dict = {
                "queryState": {role: {"projections": items} for role, items in self.roles.items()}
            }
            if self.sort:
                query["sortDefinition"] = {
                    "sort": [{"field": f, "direction": d} for f, d in self.sort],
                    "isDefaultSort": False,
                }
            visual["query"] = query
        if self.objects:
            visual["objects"] = self.objects
        container: dict = {}
        if self.title is not None:
            container["title"] = [
                {"properties": {"show": literal(True), "text": literal(self.title)}}
            ]
        if container:
            visual["visualContainerObjects"] = container
        if self.sync_group:
            visual["syncGroup"] = {
                "groupName": self.sync_group,
                "fieldChanges": True,
                "filterChanges": True,
            }
        visual["drillFilterOtherVisuals"] = True
        return {
            "$schema": VISUAL_SCHEMA,
            "name": self.name,
            "position": {
                "x": self.x,
                "y": self.y,
                "z": z,
                "height": self.height,
                "width": self.width,
                "tabOrder": z,
            },
            "visual": visual,
        }


def textbox(
    name: str,
    x: float,
    y: float,
    width: float,
    height: float,
    paragraphs: list[tuple[str, str, str, bool]],
) -> Visual:
    """Paragraphs of (text, font size, colour, bold)."""
    runs = []
    for value, size, colour, bold in paragraphs:
        style = {"fontSize": size, "color": colour}
        if bold:
            style["fontWeight"] = "bold"
        runs.append({"textRuns": [{"value": value, "textStyle": style}]})
    return Visual(
        name,
        "textbox",
        x,
        y,
        width,
        height,
        objects={"general": [{"properties": {"paragraphs": runs}}]},
    )


def page_title(prefix: str, title: str, subtitle: str) -> Visual:
    return textbox(
        f"{prefix}_title",
        24,
        8,
        1232,
        56,
        [(title, "18pt", INK, True), (subtitle, "9pt", INK_SECONDARY, False)],
    )


def slicer(
    name: str,
    entity: str,
    prop: str,
    display: str,
    x: float,
    y: float,
    width: float = 296,
    sync: str | None = None,
) -> Visual:
    return Visual(
        name,
        "slicer",
        x,
        y,
        width,
        56,
        roles={"Values": [projection(column(entity, prop), display, active=True)]},
        sort=[(column(entity, prop), "Ascending")],
        objects={"data": [{"properties": {"mode": literal("Dropdown")}}]},
        sync_group=sync,
    )


def card(
    name: str, measure_name: str, display: str, x: float, y: float, width: float, height: float = 88
) -> Visual:
    return Visual(
        name,
        "card",
        x,
        y,
        width,
        height,
        roles={"Values": [projection(measure(measure_name), display)]},
    )


CATEGORICAL_AXIS = {"categoryAxis": [{"properties": {"axisType": literal("Categorical")}}]}


def overview_page() -> list[Visual]:
    p = "overview"
    visuals = [
        page_title(
            p,
            "Spend overview",
            "Contracts notified from January 2019 to September 2026. " + SOURCE_NOTE,
        ),
        slicer(f"{p}_year", "Dates", "year", "Year", 24, 68, sync="year"),
        slicer(f"{p}_region", "Buyers", "buyer_region", "Buyer region", 336, 68, sync="region"),
        slicer(
            f"{p}_buyer_category",
            "Buyers",
            "buyer_category",
            "Buyer category",
            648,
            68,
            sync="buyer_category",
        ),
        slicer(f"{p}_contract_type", "Contracts", "contract_type", "Contract type", 960, 68),
    ]
    cards = [
        ("awarded_value", "Awarded Value", "Awarded value"),
        ("contracts", "Contracts", "Contracts"),
        ("buyers", "Buyers", "Buyers"),
        ("suppliers", "Suppliers", "Suppliers"),
        ("single_offer", "Single-offer Rate", "Single-offer rate"),
        ("sme_share", "SME Share of Value", "SME share of value"),
    ]
    for i, (key, measure_name, display) in enumerate(cards):
        visuals.append(card(f"{p}_card_{key}", measure_name, display, 24 + i * 208, 136, 192))
    visuals += [
        Visual(
            f"{p}_value_by_year",
            "columnChart",
            24,
            240,
            600,
            224,
            roles={
                "Category": [projection(column("Dates", "year"), "Year", active=True)],
                "Y": [
                    projection(measure("Contract Value"), "Contracts"),
                    projection(measure("Framework Value"), "Framework agreements (maximum)"),
                ],
            },
            sort=[(column("Dates", "year"), "Ascending")],
            title="Awarded value by year",
            objects=CATEGORICAL_AXIS,
        ),
        Visual(
            f"{p}_value_by_division",
            "barChart",
            640,
            240,
            616,
            464,
            roles={
                "Category": [
                    projection(column("CPV", "cpv_division_label"), "CPV division", active=True)
                ],
                "Y": [projection(measure("Awarded Value"), "Awarded value")],
            },
            sort=[(measure("Awarded Value"), "Descending")],
            title="Awarded value by CPV division",
        ),
        Visual(
            f"{p}_single_offer_by_year",
            "clusteredColumnChart",
            24,
            480,
            600,
            224,
            roles={
                "Category": [projection(column("Dates", "year"), "Year", active=True)],
                "Y": [
                    projection(measure("Single-offer Rate (reported years)"), "Single-offer rate")
                ],
            },
            sort=[(column("Dates", "year"), "Ascending")],
            title="Competitive procedures with a single offer (years where at least half "
            "report their offers)",
            objects=CATEGORICAL_AXIS,
        ),
    ]
    return visuals


def suppliers_page() -> list[Visual]:
    p = "suppliers"
    visuals = [
        page_title(
            p,
            "Suppliers and supply risk",
            "Amounts of contracts with several holders are shared equally between them. "
            "Names of individual entrepreneurs are withheld.",
        ),
        slicer(f"{p}_year", "Dates", "year", "Year", 24, 68, sync="year"),
        slicer(f"{p}_region", "Buyers", "buyer_region", "Buyer region", 336, 68, sync="region"),
        slicer(
            f"{p}_buyer_category",
            "Buyers",
            "buyer_category",
            "Buyer category",
            648,
            68,
            sync="buyer_category",
        ),
        slicer(f"{p}_size", "Suppliers", "holder_size", "Supplier size", 960, 68),
    ]
    cards = [
        ("suppliers", "Suppliers", "Suppliers"),
        ("top10", "Top 10 Supplier Share", "Share of the 10 largest suppliers"),
        ("hhi", "Supplier HHI", "Supplier concentration (HHI, 0 to 10,000)"),
        ("sme_share", "SME Share of Value", "SME share of value"),
    ]
    for i, (key, measure_name, display) in enumerate(cards):
        visuals.append(card(f"{p}_card_{key}", measure_name, display, 24 + i * 312, 136, 296))
    visuals += [
        Visual(
            f"{p}_largest",
            "tableEx",
            24,
            240,
            760,
            464,
            roles={
                "Values": [
                    projection(column("Suppliers", "supplier_name"), "Supplier"),
                    projection(measure("Supplier Value"), "Value"),
                    projection(measure("Supplier Contracts"), "Contracts"),
                    projection(column("Suppliers", "holder_size"), "Size"),
                    projection(column("Suppliers", "segment"), "Segment"),
                ]
            },
            sort=[(measure("Supplier Value"), "Descending")],
            title="Largest suppliers",
        ),
        Visual(
            f"{p}_segments",
            "clusteredBarChart",
            800,
            240,
            456,
            464,
            roles={
                "Category": [projection(column("Suppliers", "segment"), "Segment", active=True)],
                "Y": [projection(measure("Profiled Suppliers"), "Suppliers")],
            },
            sort=[(measure("Profiled Suppliers"), "Descending")],
            title="Suppliers per segment (at least three contracts)",
        ),
    ]
    return visuals


def categories_page() -> list[Visual]:
    p = "categories"
    return [
        page_title(
            p,
            "Categories and markets",
            "National markets: each point is a CPV group with at least 30 competitive "
            "procedures reporting their offers.",
        ),
        textbox(
            f"{p}_method",
            648,
            68,
            608,
            56,
            [
                (
                    "Value at stake: awarded value of the group, as a percentile among groups. "
                    "Supply risk: mean percentile of supplier concentration (HHI), share of "
                    "single offers and scarcity of offers. Quadrants split at the medians.",
                    "9pt",
                    INK_SECONDARY,
                    False,
                )
            ],
        ),
        slicer(f"{p}_quadrant", "CategoryMarkets", "kraljic_quadrant", "Kraljic quadrant", 24, 68),
        slicer(f"{p}_division", "CategoryMarkets", "cpv_division", "CPV division", 336, 68),
        Visual(
            f"{p}_kraljic",
            "scatterChart",
            24,
            136,
            600,
            568,
            roles={
                "Category": [
                    projection(
                        column("CategoryMarkets", "cpv_group_label"), "CPV group", active=True
                    )
                ],
                "Series": [projection(column("CategoryMarkets", "kraljic_quadrant"), "Quadrant")],
                "X": [projection(measure("Market Value Percentile"), "Value at stake")],
                "Y": [projection(measure("Market Supply Risk"), "Supply risk")],
            },
            title="Kraljic matrix of public markets",
        ),
        Visual(
            f"{p}_markets",
            "tableEx",
            640,
            136,
            616,
            568,
            roles={
                "Values": [
                    projection(column("CategoryMarkets", "cpv_group_label"), "CPV group"),
                    projection(column("CategoryMarkets", "kraljic_quadrant"), "Quadrant"),
                    projection(measure("Market Value"), "Value"),
                    projection(column("CategoryMarkets", "suppliers"), "Suppliers"),
                    projection(column("CategoryMarkets", "avg_offers"), "Average offers"),
                    projection(column("CategoryMarkets", "single_bid_share"), "Single offer"),
                    projection(column("CategoryMarkets", "supply_risk"), "Supply risk"),
                ]
            },
            sort=[(measure("Market Value"), "Descending")],
            title="Markets by value",
        ),
    ]


def alerts_page() -> list[Visual]:
    p = "alerts"
    visuals = [
        page_title(
            p,
            "Alerts to review",
            "Risk indicators computed from the published data, by priority (severity and "
            "amount at stake); most have a legitimate explanation. Buyer-level alerts "
            "ignore the year.",
        ),
        slicer(f"{p}_category", "Alerts", "category", "Category", 24, 68),
        slicer(f"{p}_severity", "Alerts", "severity", "Severity", 336, 68),
        slicer(f"{p}_label", "Alerts", "label", "Alert", 648, 68),
        slicer(f"{p}_year", "Dates", "year", "Year", 960, 68, sync="year"),
    ]
    cards = [
        ("alerts", "Alerts", "Alerts"),
        ("high", "High-severity Alerts", "High-severity alerts"),
        ("stake", "Amount at Stake", "Amount at stake (not a loss)"),
    ]
    for i, (key, measure_name, display) in enumerate(cards):
        visuals.append(card(f"{p}_card_{key}", measure_name, display, 24 + i * 416, 136, 400))
    visuals.append(
        Visual(
            f"{p}_queue",
            "tableEx",
            24,
            240,
            1232,
            464,
            roles={
                "Values": [
                    projection(column("Alerts", "priority"), "Priority"),
                    projection(column("Alerts", "severity"), "Severity"),
                    projection(column("Alerts", "label"), "Alert"),
                    projection(column("Alerts", "title"), "What was found"),
                    projection(column("Alerts", "buyer_name"), "Buyer"),
                    projection(column("Alerts", "amount_at_stake"), "At stake"),
                    projection(column("Alerts", "recommended_action"), "Recommended action"),
                ]
            },
            sort=[(column("Alerts", "priority"), "Descending")],
            title="Review queue",
        )
    )
    return visuals


PAGES = [
    ("overview", "Spend overview", overview_page),
    ("suppliers", "Suppliers", suppliers_page),
    ("categories", "Categories and markets", categories_page),
    ("alerts", "Alerts", alerts_page),
]

THEME = {
    "name": "Procurement Intelligence",
    "dataColors": PALETTE,
    "foreground": INK,
    "foregroundNeutralSecondary": INK_SECONDARY,
    "foregroundNeutralTertiary": "#898781",
    "background": "#fcfcfb",
    "backgroundLight": "#f3f2ee",
    "backgroundNeutral": "#e1e0d9",
    "tableAccent": PALETTE[0],
    "good": "#0ca30c",
    "neutral": "#fab219",
    "bad": "#d03b3b",
    "maximum": PALETTE[0],
    "center": "#c3c2b7",
    "minimum": PALETTE[1],
}


def write_report(out: Path, base_theme: Path) -> None:
    item = out / f"{NAME}.Report"
    definition = item / "definition"
    (definition / "pages").mkdir(parents=True)
    write_json(
        item / "definition.pbir",
        {
            "$schema": f"{SCHEMAS}/item/report/definitionProperties/1.0.0/schema.json",
            "version": "4.0",
            "datasetReference": {"byPath": {"path": f"../{NAME}.SemanticModel"}},
        },
    )
    write_json(
        definition / "version.json",
        {
            "$schema": f"{SCHEMAS}/item/report/definition/versionMetadata/1.0.0/schema.json",
            "version": "2.0.0",
        },
    )
    versions = {"visual": "2.2.0", "page": "2.0.0", "report": "3.0.0"}
    write_json(
        definition / "report.json",
        {
            "$schema": REPORT_SCHEMA,
            "themeCollection": {
                "baseTheme": {
                    "name": BASE_THEME,
                    "reportVersionAtImport": versions,
                    "type": "SharedResources",
                },
                "customTheme": {
                    "name": THEME_FILE,
                    "reportVersionAtImport": versions,
                    "type": "RegisteredResources",
                },
            },
            "resourcePackages": [
                {
                    "name": "SharedResources",
                    "type": "SharedResources",
                    "items": [
                        {
                            "name": BASE_THEME,
                            "path": f"BaseThemes/{BASE_THEME}.json",
                            "type": "BaseTheme",
                        }
                    ],
                },
                {
                    "name": "RegisteredResources",
                    "type": "RegisteredResources",
                    "items": [{"name": THEME_FILE, "path": THEME_FILE, "type": "CustomTheme"}],
                },
            ],
            "settings": {
                "useStylableVisualContainerHeader": True,
                "exportDataMode": "AllowSummarized",
                "defaultDrillFilterOtherVisuals": True,
                "allowChangeFilterTypes": True,
                "useEnhancedTooltips": True,
            },
        },
    )
    write_json(
        definition / "pages" / "pages.json",
        {
            "$schema": f"{SCHEMAS}/item/report/definition/pagesMetadata/1.0.0/schema.json",
            "pageOrder": [name for name, _, _ in PAGES],
            "activePageName": PAGES[0][0],
        },
    )
    for name, display, build in PAGES:
        folder = definition / "pages" / name
        write_json(
            folder / "page.json",
            {
                "$schema": PAGE_SCHEMA,
                "name": name,
                "displayName": display,
                "displayOption": "FitToPage",
                "height": 720,
                "width": 1280,
            },
        )
        for z, visual in enumerate(build()):
            write_json(folder / "visuals" / visual.name / "visual.json", visual.to_json(z * 1000))
    resources = item / "StaticResources"
    shared = resources / "SharedResources" / "BaseThemes"
    shared.mkdir(parents=True)
    shutil.copyfile(base_theme, shared / f"{BASE_THEME}.json")
    write_json(resources / "RegisteredResources" / THEME_FILE, THEME)


# --------------------------------------------------------------------- project


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Power BI Desktop writes TMDL with Windows line endings; Git normalises them.
    path.write_text(content.rstrip("\n") + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=ROOT / "powerbi")
    parser.add_argument(
        "--base-theme",
        type=Path,
        default=None,
        help=f"{BASE_THEME}.json of Power BI Desktop (default: the copy in the project)",
    )
    args = parser.parse_args()
    out: Path = args.out
    current = out / f"{NAME}.Report" / "StaticResources" / "SharedResources" / "BaseThemes"
    base_theme = args.base_theme or current / f"{BASE_THEME}.json"
    if not base_theme.exists():
        raise SystemExit(f"Base theme not found: {base_theme} (use --base-theme)")
    theme_copy = base_theme.read_bytes()
    for item in (f"{NAME}.SemanticModel", f"{NAME}.Report"):
        shutil.rmtree(out / item, ignore_errors=True)
    staged = out / f".{BASE_THEME}.json"
    staged.parent.mkdir(parents=True, exist_ok=True)
    staged.write_bytes(theme_copy)
    try:
        write_semantic_model(out)
        write_report(out, staged)
    finally:
        staged.unlink()
    write_json(
        out / f"{NAME}.pbip",
        {
            "$schema": f"{SCHEMAS}/pbip/pbipProperties/1.0.0/schema.json",
            "version": "1.0",
            "artifacts": [{"report": {"path": f"{NAME}.Report"}}],
            "settings": {"enableAutoRecovery": True},
        },
    )
    write_text(
        out / ".gitignore",
        "# Local settings and data cache written by Power BI Desktop.\n"
        "**/.pbi/localSettings.json\n**/.pbi/cache.abf\n",
    )
    print(f"Power BI project written to {out}")


if __name__ == "__main__":
    main()
