"""Download the open-data sources of the project from data.gouv.fr.

Each source is a data.gouv.fr dataset and a rule that picks one of its
resources (format and title). The data.gouv.fr API gives the current URL of the
resource, its last modification date and its size; the file is streamed to
disk and hashed. The metadata of every download is written to
`<out>/downloads.json`, which build_snapshot.py copies into the manifest.

Usage: python scripts/snapshot/download.py --out raw
Standard library only, so that it runs before the project is installed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

API = "https://www.data.gouv.fr/api/1/datasets/{dataset}/"
USER_AGENT = "ai-procurement-intelligence (https://github.com/Thierry-Daconceicao-Alvares)"

SOURCES = {
    "decp": {
        "name": "Données essentielles de la commande publique consolidées (format tabulaire)",
        "dataset": "608c055b35eb4e6ee20eb325",
        "page": "https://www.data.gouv.fr/datasets/608c055b35eb4e6ee20eb325",
        "license": "Licence Ouverte / Open Licence 2.0",
        "producer": "Colin Maudry (decp.info), from the data published by French public buyers",
        "format": "parquet",
        "title": r"consolid",
        "exclude": r"documentation|sch[ée]ma",
        "fallback_url": "https://www.data.gouv.fr/api/1/datasets/r/11cea8e8-df3e-4ed1-932b-781e2635e432",
        "file": "decp.parquet",
    },
    "sirene": {
        "name": "Base Sirene des entreprises et de leurs établissements, stock des unités légales",
        "dataset": "5b7ffc618b4c4169d30727e0",
        "page": "https://www.data.gouv.fr/datasets/5b7ffc618b4c4169d30727e0",
        "license": "Licence Ouverte / Open Licence 2.0",
        "producer": "INSEE",
        "format": "parquet",
        "title": r"StockUniteLegale",
        "exclude": r"historique",
        "fallback_url": "https://www.data.gouv.fr/api/1/datasets/r/350182c9-148a-46e0-8389-76c2ec1374a3",
        "file": "sirene_unites_legales.parquet",
    },
}


def fetch_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def pick_resource(source: dict, resources: list[dict]) -> dict | None:
    """The largest resource in the expected format whose title matches."""
    candidates = [
        resource
        for resource in resources
        if (resource.get("format") or "").lower() == source["format"]
        and re.search(source["title"], resource.get("title") or "", re.IGNORECASE)
        and not re.search(source["exclude"], resource.get("title") or "", re.IGNORECASE)
    ]
    return max(candidates, key=lambda r: r.get("filesize") or 0, default=None)


def resolve(source: dict) -> dict:
    """Resource metadata from the API, or the known stable URL if the API
    does not list a matching resource."""
    try:
        dataset = fetch_json(API.format(dataset=source["dataset"]))
    except OSError as exc:
        print(f"API unavailable ({exc}); using the stable URL", file=sys.stderr)
        return {"url": source["fallback_url"], "title": None, "id": None}
    resource = pick_resource(source, dataset.get("resources") or [])
    if resource is None:
        titles = "; ".join(
            f"{r.get('title')} ({r.get('format')})" for r in dataset.get("resources") or []
        )
        print(f"No resource matches {source['title']!r}. Resources: {titles}", file=sys.stderr)
        return {"url": source["fallback_url"], "title": None, "id": None}
    return resource


def download(url: str, path: Path, attempts: int = 3) -> tuple[str, int]:
    """Stream `url` to `path`; return its SHA-256 and size."""
    for attempt in range(1, attempts + 1):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            digest, size, last_report = hashlib.sha256(), 0, 0
            with urllib.request.urlopen(request, timeout=120) as response, path.open("wb") as out:
                while chunk := response.read(1 << 20):
                    out.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
                    if size - last_report >= 100 << 20:
                        print(f"  {size / 1e6:,.0f} MB", flush=True)
                        last_report = size
            return digest.hexdigest(), size
        except OSError as exc:
            if attempt == attempts:
                raise
            print(f"Download failed ({exc}); retry {attempt}/{attempts - 1}", file=sys.stderr)
            time.sleep(10 * attempt)
    raise AssertionError("unreachable")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=Path("raw"))
    parser.add_argument("--only", choices=sorted(SOURCES), action="append")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    records = {}
    for key in args.only or SOURCES:
        source = SOURCES[key]
        resource = resolve(source)
        path = args.out / source["file"]
        print(f"Downloading {source['name']} from {resource['url']}", flush=True)
        started = datetime.now(UTC)
        sha256, size = download(resource["url"], path)
        print(f"  {size / 1e6:,.1f} MB, SHA-256 {sha256}", flush=True)
        records[key] = {
            "name": source["name"],
            "producer": source["producer"],
            "license": source["license"],
            "dataset_page": source["page"],
            "resource_id": resource.get("id"),
            "resource_title": resource.get("title"),
            "resource_last_modified": resource.get("last_modified"),
            "url": resource["url"],
            "downloaded_at": started.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "bytes": size,
            "sha256": sha256,
            "file": source["file"],
        }
    (args.out / "downloads.json").write_text(
        json.dumps(records, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
