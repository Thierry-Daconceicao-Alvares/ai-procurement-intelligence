"""The DuckDB warehouse: runs the SQL models in order and exports the marts."""

from __future__ import annotations

import logging
import time
from pathlib import Path

import duckdb

from .config import Settings

logger = logging.getLogger(__name__)

SQL_DIR = Path(__file__).parent / "sql"


def connect(settings: Settings, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    settings.warehouse_path.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(settings.warehouse_path), read_only=read_only)


def render(sql: str, settings: Settings) -> str:
    """Fill the placeholders of a SQL model. Values come from the settings,
    never from user input."""
    values = {
        "snapshot_dir": settings.snapshot_dir.as_posix(),
        "reference_dir": settings.reference_dir.as_posix(),
        "start_date": settings.start_date.isoformat(),
        "end_date": settings.end_date.isoformat(),
        "snapshot_date": settings.snapshot_date.isoformat(),
    }
    for key, value in values.items():
        sql = sql.replace("{" + key + "}", value)
    return sql


def run_models(
    con: duckdb.DuckDBPyConnection, settings: Settings, names: list[str] | None = None
) -> None:
    """Run the SQL models of sql/ in file order (or only `names`)."""
    for path in sorted(SQL_DIR.glob("*.sql")):
        if names and path.stem not in names:
            continue
        started = time.perf_counter()
        con.execute(render(path.read_text(encoding="utf-8"), settings))
        logger.info("%s done in %.1f s", path.name, time.perf_counter() - started)
