"""Build reference/cpv_2008.csv from the official CPV 2008 code list.

Source: the Genericode file of the eForms SDK published by the Publications
Office of the European Union (CC BY 4.0), pinned to a release. The CSV keeps
the code, its parent, its level in the hierarchy and its English and French
labels.

Usage: python scripts/reference/build_cpv.py
"""

from __future__ import annotations

import csv
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

SDK_VERSION = "1.15.1"
URL = f"https://raw.githubusercontent.com/OP-TED/eForms-SDK/{SDK_VERSION}/codelists/cpv.gc"
OUT = Path(__file__).resolve().parents[2] / "reference" / "cpv_2008.csv"

LEVELS = {6: "division", 5: "group", 4: "class", 3: "category"}


def level(code: str) -> str:
    """CPV codes are 8 digits; trailing zeros give the depth of the code:
    XX000000 is a division, XXX00000 a group, XXXX0000 a class, XXXXX000 a
    category, and longer codes are subcategories."""
    trailing = len(code) - len(code.rstrip("0"))
    return LEVELS.get(min(trailing, 6), "subcategory") if trailing >= 3 else "subcategory"


def main() -> int:
    with urllib.request.urlopen(URL, timeout=120) as response:
        root = ET.fromstring(response.read())
    rows = []
    for row in root.find("SimpleCodeList").findall("Row"):
        values = {}
        for value in row.findall("Value"):
            simple = value.find("SimpleValue")
            values[value.get("ColumnRef")] = simple.text if simple is not None else None
        code = values["code"]
        rows.append(
            {
                "code": code,
                "parent_code": values.get("parentCode") or "",
                "level": level(code),
                "division": code[:2],
                "label_en": values.get("eng_label") or values.get("Name") or "",
                "label_fr": values.get("fra_label") or "",
            }
        )
    rows.sort(key=lambda r: r["code"])
    with OUT.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"{len(rows)} CPV codes written to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
