"""Freeze the downloaded sources into data/snapshot/.

Outputs:
- decp/part-<n>.parquet: every row of the consolidated DECP, sorted by
  notification date, without the columns the project does not use, in files
  of about 35 MB (GitHub recommends files under 50 MB);
- sirene_titulaires.parquet: the SIRENE legal units of the contract holders,
  without any name (individual entrepreneurs are natural persons);
- manifest.json: sources, download dates and SHA-256 of the downloaded files,
  plus the row count, size and SHA-256 of every file written;
- SUMMARY.txt: one line for the workflow summary.

Usage: python scripts/snapshot/build_snapshot.py --raw raw --out data/snapshot
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import duckdb

# Columns of the consolidated DECP that the project does not use: links to the
# source files and geographic coordinates (departments and regions are kept).
DECP_DROPPED = {
    "sourceFile",
    "acheteur_latitude",
    "acheteur_longitude",
    "titulaire_latitude",
    "titulaire_longitude",
}

# SIRENE fields kept for the contract holders. Names and first names are never
# kept: an individual entrepreneur is a natural person.
SIRENE_KEPT = [
    "siren",
    "statutDiffusionUniteLegale",
    "unitePurgeeUniteLegale",
    "dateCreationUniteLegale",
    "trancheEffectifsUniteLegale",
    "anneeEffectifsUniteLegale",
    "dateDernierTraitementUniteLegale",
    "categorieEntreprise",
    "anneeCategorieEntreprise",
    "dateDebut",
    "etatAdministratifUniteLegale",
    "categorieJuridiqueUniteLegale",
    "activitePrincipaleUniteLegale",
    "nomenclatureActivitePrincipaleUniteLegale",
    "economieSocialeSolidaireUniteLegale",
    "societeMissionUniteLegale",
    "caractereEmployeurUniteLegale",
]
SIRENE_REQUIRED = {"siren", "categorieJuridiqueUniteLegale", "etatAdministratifUniteLegale"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def columns(con: duckdb.DuckDBPyConnection, path: Path) -> list[str]:
    rows = con.execute("DESCRIBE SELECT * FROM read_parquet(?)", [str(path)]).fetchall()
    return [row[0] for row in rows]


def write_decp(con: duckdb.DuckDBPyConnection, source: Path, out: Path) -> dict:
    available = columns(con, source)
    kept = [name for name in available if name not in DECP_DROPPED]
    order = [name for name in ("dateNotification", "uid", "modification_id") if name in kept]
    target = out / "decp"
    shutil.rmtree(target, ignore_errors=True)
    con.execute(
        f"""
        COPY (
            SELECT {", ".join(quote(name) for name in kept)}
            FROM read_parquet('{source}')
            ORDER BY {", ".join(quote(name) for name in order) or "ALL"}
        ) TO '{target}' (
            FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 50000,
            FILE_SIZE_BYTES '30MB', FILENAME_PATTERN 'part-{{i}}'
        )
        """
    )
    files = sorted(target.glob("*.parquet"), key=lambda p: int(p.stem.split("-")[1]))
    stats = con.execute(
        f"""
        SELECT count(*), count(DISTINCT uid),
               min(dateNotification)::VARCHAR, max(dateNotification)::VARCHAR
        FROM read_parquet('{target}/*.parquet')
        """
    ).fetchone()
    return {
        "rows": stats[0],
        "contracts": stats[1],
        "notification_dates": {"min": stats[2], "max": stats[3]},
        "columns": kept,
        "dropped_columns": sorted(DECP_DROPPED & set(available)),
        "files": [str(path.relative_to(out)) for path in files],
    }


def write_sirene(con: duckdb.DuckDBPyConnection, decp: Path, source: Path, out: Path) -> dict:
    available = set(columns(con, source))
    missing = SIRENE_REQUIRED - available
    if missing:
        raise SystemExit(
            f"The SIRENE file lacks {sorted(missing)}: it is not the stock of legal units."
        )
    kept = [name for name in SIRENE_KEPT if name in available]
    con.execute(
        f"""
        CREATE TEMP TABLE holders AS
        SELECT DISTINCT substr(digits, 1, 9) AS siren
        FROM (
            SELECT regexp_replace(titulaire_id, '[^0-9]', '', 'g') AS digits
            FROM read_parquet('{decp}/*.parquet')
            WHERE titulaire_id IS NOT NULL
        )
        WHERE length(digits) IN (9, 14)
        """
    )
    target = out / "sirene_titulaires.parquet"
    con.execute(
        f"""
        COPY (
            SELECT {", ".join(quote(name) for name in kept)}
            FROM read_parquet('{source}') AS sirene
            WHERE sirene.siren IN (SELECT siren FROM holders)
            ORDER BY siren
        ) TO '{target}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """
    )
    requested = con.execute("SELECT count(*) FROM holders").fetchone()[0]
    found = con.execute(f"SELECT count(*) FROM read_parquet('{target}')").fetchone()[0]
    return {"sirens_requested": requested, "rows": found, "columns": kept}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--raw", type=Path, default=Path("raw"))
    parser.add_argument("--out", type=Path, default=Path("data/snapshot"))
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    downloads = json.loads((args.raw / "downloads.json").read_text(encoding="utf-8"))

    con = duckdb.connect()
    decp = write_decp(con, args.raw / downloads["decp"]["file"], args.out)
    sirene = write_sirene(con, args.out / "decp", args.raw / downloads["sirene"]["file"], args.out)
    files = []
    for path in sorted(args.out.rglob("*.parquet")):
        rows = con.execute(f"SELECT count(*) FROM read_parquet('{path}')").fetchone()[0]
        files.append(
            {
                "path": str(path.relative_to(args.out)),
                "rows": rows,
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
        )
    manifest = {
        "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sources": downloads,
        "decp": decp,
        "sirene": sirene,
        "files": files,
    }
    (args.out / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    total_mb = sum(f["bytes"] for f in files) / 1e6
    summary = (
        f"DECP: {decp['rows']:,} rows ({decp['contracts']:,} contracts), notified from "
        f"{decp['notification_dates']['min']} to {decp['notification_dates']['max']}; "
        f"SIRENE: {sirene['rows']:,} of {sirene['sirens_requested']:,} holders found; "
        f"{len(files)} files, {total_mb:,.0f} MB."
    )
    (args.out / "SUMMARY.txt").write_text(summary + "\n", encoding="utf-8")
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
