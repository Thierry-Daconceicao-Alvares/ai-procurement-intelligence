"""Build the warehouse: SQL models and Python steps, in order."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

import duckdb

from . import warehouse
from .alerts import build_alerts
from .amount_model import build_amount_model
from .classifier import build_classifier
from .config import Settings
from .dedup import build_duplicates
from .evaluation import amount_injection
from .export import data_quality_funnel, export_marts
from .segmentation import build_segments

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Step:
    name: str
    run: Callable[[duckdb.DuckDBPyConnection, Settings], object]


def sql(name: str) -> Step:
    return Step(name, lambda con, settings: warehouse.run_models(con, settings, [name]))


def save_metrics(name: str, settings: Settings, metrics: dict) -> None:
    """Model metrics are written to reports/metrics/<name>.json and committed:
    they are the reproducible results quoted in the README."""
    settings.metrics_dir.mkdir(parents=True, exist_ok=True)
    path = settings.metrics_dir / f"{name}.json"
    path.write_text(json.dumps(metrics, indent=2, default=str) + "\n", encoding="utf-8")


def amount_model_step(con: duckdb.DuckDBPyConnection, settings: Settings) -> None:
    save_metrics("amount_model", settings, build_amount_model(con))


def classifier_step(con: duckdb.DuckDBPyConnection, settings: Settings) -> None:
    save_metrics("cpv_classifier", settings, build_classifier(con, settings.model_dir))


def segments_step(con: duckdb.DuckDBPyConnection, settings: Settings) -> None:
    save_metrics("segments", settings, build_segments(con))


def alerts_step(con: duckdb.DuckDBPyConnection, settings: Settings) -> None:
    alerts = build_alerts(con)
    counts = alerts.groupby(["category", "alert_type", "severity"]).size()
    save_metrics(
        "alerts",
        settings,
        {
            "total": len(alerts),
            "by_type": {
                f"{category} / {kind} / {severity}": int(n)
                for (category, kind, severity), n in counts.items()
            },
        },
    )


def export_step(con: duckdb.DuckDBPyConnection, settings: Settings) -> None:
    save_metrics("data_quality", settings, data_quality_funnel(con))
    save_metrics("marts_size_mb", settings, export_marts(con, settings))


STEPS = [
    sql("01_staging"),
    sql("02_contracts"),
    sql("03_duplicate_candidates"),
    Step("duplicates", lambda con, settings: build_duplicates(con)),
    sql("04_amount_quality"),
    Step("amount_model", amount_model_step),
    sql("05_marts"),
    sql("06_alerts"),
    Step("classifier", classifier_step),
    Step("segments", segments_step),
    Step("alerts", alerts_step),
    Step("export", export_step),
]


def build(settings: Settings, only: list[str] | None = None) -> None:
    """Run the steps in order. Each step gets its own connection: closing it
    releases DuckDB's memory before the next step, which may be a Python model
    that needs it."""
    for step in STEPS:
        if only and step.name not in only:
            continue
        started = time.perf_counter()
        con = warehouse.connect(settings)
        try:
            step.run(con, settings)
        finally:
            con.close()
        logger.info("%s: %.1f s", step.name, time.perf_counter() - started)


def evaluate(settings: Settings) -> None:
    """Measure the amount detectors on injected errors; the warehouse must be
    built. Writes reports/metrics/evaluation_amounts.json."""
    con = warehouse.connect(settings)
    try:
        save_metrics("evaluation_amounts", settings, amount_injection(con))
    finally:
        con.close()
