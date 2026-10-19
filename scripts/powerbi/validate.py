"""Validate the Power BI project (PBIP) in powerbi/ without Power BI Desktop.

Two kinds of checks:
- references (no download needed): every table, column and measure that a
  visual, a relationship, a sort order or a DAX expression uses exists in the
  TMDL files; pages and visuals are consistent with their folders;
- JSON schemas (with --schemas DIR, a clone of github.com/microsoft/json-schemas):
  every JSON file that declares a $schema is valid against Microsoft's
  published schema for Power BI items.

The TMDL itself is parsed by the Tabular Object Model in CI (tmdl-check/).

Usage: python scripts/powerbi/validate.py [--project powerbi] [--schemas DIR]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PREFIX = "https://developer.microsoft.com/json-schemas/"
NAME = r"(?:'(?:[^']|'')+'|[^\s'=.\[\]]+)"


def unquote(name: str) -> str:
    name = name.strip()
    if name.startswith("'") and name.endswith("'"):
        return name[1:-1].replace("''", "'")
    return name


@dataclass
class SemanticModel:
    """Names declared by the TMDL files of a semantic model."""

    tables: dict[str, set[str]] = field(default_factory=dict)  # table -> columns
    measures: dict[str, str] = field(default_factory=dict)  # measure -> table
    expressions: dict[str, str] = field(default_factory=dict)  # measure -> DAX
    sort_by: list[tuple[str, str, str]] = field(default_factory=list)
    relationships: list[tuple[str, str, str, str]] = field(default_factory=list)
    refs: list[str] = field(default_factory=list)
    query_order: list[str] = field(default_factory=list)
    parameters: set[str] = field(default_factory=set)


def split_column_ref(value: str) -> tuple[str, str]:
    """'Table Name'.'Column' or Table.Column -> (table, column)."""
    match = re.fullmatch(rf"({NAME})\.({NAME})", value.strip())
    if not match:
        raise ValueError(f"Cannot read the column reference {value!r}")
    return unquote(match.group(1)), unquote(match.group(2))


def read_model(definition: Path) -> SemanticModel:
    model = SemanticModel()
    for path in sorted((definition / "tables").glob("*.tmdl")):
        table = None
        measure = None
        for line in path.read_text(encoding="utf-8").splitlines():
            if match := re.match(rf"^table ({NAME})\s*$", line):
                table = unquote(match.group(1))
                model.tables[table] = set()
                measure = None
            elif match := re.match(rf"^\tcolumn ({NAME})(\s*=.*)?$", line):
                model.tables[table].add(unquote(match.group(1)))
                measure = None
            elif match := re.match(rf"^\tmeasure ({NAME})\s*=\s*(.*)$", line):
                measure = unquote(match.group(1))
                model.measures[measure] = table
                model.expressions[measure] = match.group(2)
            elif measure and line.startswith("\t\t\t"):
                model.expressions[measure] += "\n" + line.strip()
            elif match := re.match(r"^\t\tsortByColumn: (.+)$", line):
                model.sort_by.append((table, path.name, unquote(match.group(1))))
            elif not line.startswith("\t\t"):
                measure = None
    relationships = definition / "relationships.tmdl"
    if relationships.exists():
        current: dict[str, tuple[str, str]] = {}
        for line in relationships.read_text(encoding="utf-8").splitlines():
            if match := re.match(r"^\t(fromColumn|toColumn): (.+)$", line):
                current[match.group(1)] = split_column_ref(match.group(2))
                if len(current) == 2:
                    (ft, fc), (tt, tc) = current["fromColumn"], current["toColumn"]
                    model.relationships.append((ft, fc, tt, tc))
                    current = {}
    model_file = (definition / "model.tmdl").read_text(encoding="utf-8")
    model.refs = [unquote(m) for m in re.findall(rf"^ref table ({NAME})\s*$", model_file, re.M)]
    if match := re.search(r"^annotation PBI_QueryOrder = (.+)$", model_file, re.M):
        model.query_order = json.loads(match.group(1))
    expressions = definition / "expressions.tmdl"
    if expressions.exists():
        model.parameters = {
            unquote(m)
            for m in re.findall(
                rf"^expression ({NAME}) =", expressions.read_text(encoding="utf-8"), re.M
            )
        }
    return model


def check_model(model: SemanticModel) -> list[str]:
    errors = []
    names = set(model.tables)
    if sorted(model.refs) != sorted(names):
        errors.append(f"model.tmdl refs {sorted(model.refs)} differ from tables {sorted(names)}")
    for name in model.query_order:
        if name not in names and name not in model.parameters:
            errors.append(f"PBI_QueryOrder names an unknown query: {name}")
    for name in model.parameters & names:
        errors.append(f"An expression and a table share the name {name}")
    for table, file, column in model.sort_by:
        if column not in model.tables.get(table, set()):
            errors.append(f"{file}: sortByColumn {column} is not a column of {table}")
    for ft, fc, tt, tc in model.relationships:
        for table, column in ((ft, fc), (tt, tc)):
            if column not in model.tables.get(table, set()):
                errors.append(f"relationships.tmdl: {table}[{column}] does not exist")
    for measure, table in model.measures.items():
        if measure in model.tables.get(table, set()):
            errors.append(f"Measure {measure} has the name of a column of {table}")
    errors += check_dax(model)
    return errors


def check_dax(model: SemanticModel) -> list[str]:
    """Table[Column] references must exist; [Name] alone must be a measure."""
    errors = []
    for measure, expression in model.expressions.items():
        code = re.sub(r'"(?:[^"]|"")*"', '""', expression)  # ignore string literals
        for table, column in re.findall(r"('(?:[^']|'')+'|\b\w+)\[([^\]]+)\]", code):
            table = unquote(table)
            if table not in model.tables:
                errors.append(f"[{measure}] uses the unknown table {table}")
            elif column not in model.tables[table] and column not in model.measures:
                errors.append(f"[{measure}] uses the unknown column {table}[{column}]")
        for name in re.findall(r"(?<![\w'\]])\[([^\]]+)\]", code):
            if name not in model.measures:
                errors.append(f"[{measure}] uses the unknown measure [{name}]")
    return errors


def walk(node: object) -> Iterator[dict]:
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from walk(value)


def field_refs(visual: dict) -> Iterator[tuple[str, str, str]]:
    """(kind, entity, property) of every model field used by a visual."""
    for node in walk(visual):
        for kind in ("Column", "Measure", "Hierarchy"):
            ref = node.get(kind)
            if isinstance(ref, dict) and "Property" in ref:
                source = ref.get("Expression", {}).get("SourceRef", {})
                if "Entity" in source:
                    yield kind, source["Entity"], ref["Property"]


def check_report(report: Path, model: SemanticModel) -> list[str]:
    errors = []
    pages_dir = report / "definition" / "pages"
    pages = json.loads((pages_dir / "pages.json").read_text(encoding="utf-8"))
    folders = sorted(p.name for p in pages_dir.iterdir() if p.is_dir())
    if sorted(pages.get("pageOrder", [])) != folders:
        errors.append(f"pages.json pageOrder {pages.get('pageOrder')} differs from {folders}")
    if pages.get("activePageName") not in folders:
        errors.append(f"activePageName {pages.get('activePageName')} is not a page")
    for folder in folders:
        page = json.loads((pages_dir / folder / "page.json").read_text(encoding="utf-8"))
        if page["name"] != folder:
            errors.append(f"Page folder {folder} holds the page {page['name']}")
        width, height = page.get("width", 1280), page.get("height", 720)
        for path in sorted((pages_dir / folder / "visuals").glob("*/visual.json")):
            visual = json.loads(path.read_text(encoding="utf-8"))
            label = f"{folder}/{path.parent.name}"
            if visual["name"] != path.parent.name:
                errors.append(f"{label}: the folder does not match the name {visual['name']}")
            position = {k: visual.get("position", {}).get(k, 0) for k in "xy"}
            position |= {k: visual.get("position", {}).get(k, 0) for k in ("width", "height")}
            if (
                position["x"] + position["width"] > width
                or position["y"] + position["height"] > height
            ):
                errors.append(f"{label}: the visual goes beyond the page")
            for kind, entity, prop in field_refs(visual):
                if kind == "Measure":
                    if model.measures.get(prop) != entity:
                        errors.append(f"{label}: unknown measure {entity}.{prop}")
                elif kind == "Column" and prop not in model.tables.get(entity, set()):
                    errors.append(f"{label}: unknown column {entity}.{prop}")
    return errors


def check_references(project: Path) -> list[str]:
    """All the reference checks of the project; an empty list when it is valid."""
    model_dir = next(project.glob("*.SemanticModel"))
    report_dir = next(project.glob("*.Report"))
    model = read_model(model_dir / "definition")
    errors = check_model(model)
    errors += check_report(report_dir, model)
    pbir = json.loads((report_dir / "definition.pbir").read_text(encoding="utf-8"))
    target = (report_dir / pbir["datasetReference"]["byPath"]["path"]).resolve()
    if target != model_dir.resolve():
        errors.append(f"definition.pbir points to {target}, not to {model_dir}")
    for pbip in project.glob("*.pbip"):
        for artifact in json.loads(pbip.read_text(encoding="utf-8"))["artifacts"]:
            if not (project / artifact["report"]["path"]).is_dir():
                errors.append(f"{pbip.name} points to a missing report")
    return errors


def json_files(project: Path) -> Iterator[Path]:
    for path in sorted(project.rglob("*")):
        if path.is_file() and path.suffix in {".json", ".pbir", ".pbism", ".pbip"}:
            yield path


def check_schemas(project: Path, schemas: Path) -> tuple[list[str], int]:
    """Validate every file that declares a $schema; returns (errors, files checked)."""
    import jsonschema
    from referencing import Registry, Resource
    from referencing.exceptions import NoSuchResource
    from referencing.jsonschema import DRAFT7

    def retrieve(uri: str) -> Resource:
        if not uri.startswith(SCHEMA_PREFIX):
            raise NoSuchResource(ref=uri)
        path = schemas / uri[len(SCHEMA_PREFIX) :]
        if not path.exists():
            raise NoSuchResource(ref=uri)
        contents = json.loads(path.read_text(encoding="utf-8"))
        return Resource.from_contents(contents, default_specification=DRAFT7)

    registry = Registry(retrieve=retrieve)
    errors, checked = [], 0
    for path in json_files(project):
        instance = json.loads(path.read_text(encoding="utf-8"))
        uri = instance.get("$schema") if isinstance(instance, dict) else None
        if not uri:
            continue
        try:
            schema = registry.get_or_retrieve(uri).value.contents
        except Exception as exc:  # the schema is not in the clone
            errors.append(f"{path.relative_to(project)}: schema not found ({exc})")
            continue
        validator = jsonschema.validators.validator_for(schema)(schema, registry=registry)
        for error in sorted(validator.iter_errors(instance), key=lambda e: list(e.path)):
            where = "/".join(str(p) for p in error.absolute_path) or "(root)"
            errors.append(f"{path.relative_to(project)}: {where}: {error.message[:300]}")
        checked += 1
    return errors, checked


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--project", type=Path, default=ROOT / "powerbi")
    parser.add_argument("--schemas", type=Path, help="clone of microsoft/json-schemas")
    args = parser.parse_args()
    errors = check_references(args.project)
    print(f"References: {'OK' if not errors else f'{len(errors)} error(s)'}")
    if args.schemas:
        schema_errors, checked = check_schemas(args.project, args.schemas)
        state = "OK" if not schema_errors else f"{len(schema_errors)} error(s)"
        print(f"JSON schemas: {checked} files checked, {state}")
        errors += schema_errors
    for error in errors:
        print(f"::error::{error}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
