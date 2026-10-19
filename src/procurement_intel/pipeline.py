"""Build the warehouse: SQL models and Python steps, in order."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

import duckdb

from . import warehouse
from .amount_model import build_amount_model
from .config import Settings
from .dedup import build_duplicates

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Step:
    name: str
    run: Callable[[duckdb.DuckDBPyConnection, Settings], object]


def sql(name: str) -> Step:
    return Step(name, lambda con, settings: warehouse.run_models(con, settings, [name]))


STEPS = [
    sql("01_staging"),
    sql("02_contracts"),
    sql("03_duplicate_candidates"),
    Step("duplicates", lambda con, settings: build_duplicates(con)),
    sql("04_amount_quality"),
    Step("amount_model", lambda con, settings: build_amount_model(con)),
    sql("05_marts"),
    sql("06_alerts"),
]


def build(settings: Settings, only: list[str] | None = None) -> None:
    con = warehouse.connect(settings)
    try:
        for step in STEPS:
            if only and step.name not in only:
                continue
            started = time.perf_counter()
            step.run(con, settings)
            logger.info("%s: %.1f s", step.name, time.perf_counter() - started)
    finally:
        con.close()
