"""Every page of the application runs on the marts without an exception."""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from procurement_intel.config import Settings

APP = Path(__file__).resolve().parents[1] / "app" / "streamlit_app.py"
PAGES = [
    "views/overview.py",
    "views/categories.py",
    "views/suppliers.py",
    "views/alerts.py",
    "views/classification.py",
    "views/decision_log.py",
    "views/data_quality.py",
]

pytestmark = pytest.mark.skipif(
    not (Settings.from_env().marts_dir / "contracts").exists(), reason="the marts are not built"
)


@pytest.fixture
def public_demo(monkeypatch):
    monkeypatch.setenv("PUBLIC_DEMO", "true")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


@pytest.mark.parametrize("page", PAGES)
def test_page_runs(page, public_demo):
    app = AppTest.from_file(str(APP), default_timeout=180)
    app.run()
    app.switch_page(page)
    app.run()
    assert not app.exception, app.exception
    assert app.title


def test_review_queue_lists_alerts_by_default(public_demo):
    app = AppTest.from_file(str(APP), default_timeout=180)
    app.run()
    app.switch_page("views/alerts.py")
    app.run()
    assert not app.exception
    assert app.dataframe
    assert any("alerts shown" in caption.value for caption in app.caption)


def test_concurrent_sessions_read_their_own_results():
    """Sessions run in parallel threads: each query must get its own result."""
    import sys
    from concurrent.futures import ThreadPoolExecutor

    sys.path.insert(0, str(APP.parent))
    import ui

    queries = {n: f"SELECT {n} AS n, count(*) FROM range({n} * 200000)" for n in range(1, 9)}

    def run(n):
        return ui.connection().execute(queries[n]).fetchone()[0]

    with ThreadPoolExecutor(max_workers=8) as pool:
        for _ in range(5):
            assert list(pool.map(run, queries)) == list(queries)
