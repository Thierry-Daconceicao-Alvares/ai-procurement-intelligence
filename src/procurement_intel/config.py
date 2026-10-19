"""Settings, read from environment variables (or a `.env` file)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _read(name: str, default: str) -> str:
    value = os.environ.get(name, "").strip()
    return value or default


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:  # optional dependency
        return
    load_dotenv(PROJECT_ROOT / ".env", override=False)


@dataclass(frozen=True)
class Settings:
    data_dir: Path = PROJECT_ROOT / "data"
    reference_dir: Path = PROJECT_ROOT / "reference"
    # Contracts notified in this window are analysed. The window ends with the
    # last complete quarter before the snapshot: recent months are incomplete
    # because buyers publish their data with a delay.
    start_date: date = date(2019, 1, 1)
    end_date: date = date(2026, 9, 30)
    gemini_api_key: str = ""
    generation_model: str = "gemini-3.5-flash-lite"
    public_demo: bool = False
    # Memory DuckDB may use while building the warehouse.
    duckdb_memory_limit: str = "4GB"

    @property
    def snapshot_dir(self) -> Path:
        return self.data_dir / "snapshot"

    @property
    def snapshot_date(self) -> date:
        """Date of the data snapshot, from its manifest."""
        try:
            manifest = json.loads((self.snapshot_dir / "manifest.json").read_text(encoding="utf-8"))
            return date.fromisoformat(manifest["created_at"][:10])
        except (OSError, KeyError, ValueError):
            return date.today()

    @property
    def marts_dir(self) -> Path:
        return self.data_dir / "marts"

    @property
    def model_dir(self) -> Path:
        return PROJECT_ROOT / "models"

    @property
    def metrics_dir(self) -> Path:
        return PROJECT_ROOT / "reports" / "metrics"

    @property
    def warehouse_path(self) -> Path:
        return self.data_dir / "cache" / "warehouse.duckdb"

    @classmethod
    def from_env(cls) -> Settings:
        _load_dotenv()
        data_dir = Path(_read("DATA_DIR", str(PROJECT_ROOT / "data")))
        return cls(
            data_dir=data_dir,
            start_date=date.fromisoformat(_read("START_DATE", "2019-01-01")),
            end_date=date.fromisoformat(_read("END_DATE", "2026-09-30")),
            gemini_api_key=_read("GEMINI_API_KEY", ""),
            generation_model=_read("GENERATION_MODEL", "gemini-3.5-flash-lite"),
            public_demo=_read("PUBLIC_DEMO", "false").lower() in {"1", "true", "yes"},
            duckdb_memory_limit=_read("DUCKDB_MEMORY_LIMIT", "4GB"),
        )
