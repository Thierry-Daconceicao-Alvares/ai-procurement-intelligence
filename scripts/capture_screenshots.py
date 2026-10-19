"""Capture the README screenshots and the demo GIF from the running application.

Usage (Playwright with Chromium, and ffmpeg for the GIF):
    pip install -e ".[app,dev]" && playwright install chromium
    python scripts/capture_screenshots.py --output docs/images

The script starts the application in public demo mode on the committed marts,
opens every page, then captures the review of the alert with the highest
priority, frame by frame: its evidence, its explanation and a decision.
Nothing is staged: the pages show the data as any visitor sees it.
"""

from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
PAGES = {
    "overview": "",
    "categories": "categories",
    "suppliers": "suppliers",
    "alerts": "alerts",
    "classification": "classification",
    "data-quality": "data_quality",
}
VIEWPORT = {"width": 1440, "height": 900}


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for(url: str, timeout_s: float = 90) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(1)
    raise TimeoutError(f"{url} did not answer within {timeout_s} s")


BUSY = '[data-testid="stStatusWidget"], [data-testid="stSpinner"], [data-testid="stSkeleton"]'


def settle(page: Page, extra_ms: int = 1500) -> None:
    """Wait until Streamlit has finished running the page: no status widget
    (running / stop), no spinner and no skeleton left, twice in a row."""
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(800)
    quiet = 0
    for _ in range(360):
        quiet = quiet + 1 if page.locator(BUSY).count() == 0 else 0
        if quiet >= 2:
            break
        page.wait_for_timeout(500)
    page.wait_for_timeout(extra_ms)
    errors = page.locator('[data-testid="stException"]')
    if errors.count():
        raise RuntimeError(errors.first.inner_text())


def open_page(page: Page, base: str, path: str) -> None:
    page.goto(f"{base}/{path}")
    settle(page)


def select_first_row(page: Page, table_index: int = 0) -> None:
    """Tick the selection box of the first row of a dataframe (drawn on a canvas)."""
    grid = page.locator('[data-testid="stDataFrame"]').nth(table_index)
    grid.scroll_into_view_if_needed()
    box = grid.bounding_box()
    if box is None:
        raise RuntimeError("The table is not visible.")
    page.mouse.click(box["x"] + 18, box["y"] + 35 + 18)
    settle(page, 2500)


def screenshots(page: Page, base: str, output: Path) -> None:
    for name, path in PAGES.items():
        open_page(page, base, path)
        page.screenshot(path=output / f"{name}.png", full_page=False)
        print(f"{name}.png", flush=True)
    open_page(page, base, "alerts")
    select_first_row(page)
    page.get_by_text("Explanation", exact=True).first.scroll_into_view_if_needed()
    page.wait_for_timeout(800)
    page.screenshot(path=output / "alert-review.png", full_page=False)
    print("alert-review.png", flush=True)


class Frames:
    """Screenshots of a sequence, each shown for a given time in the GIF."""

    def __init__(self, page: Page, folder: Path) -> None:
        self.page, self.folder = page, folder
        self.items: list[tuple[Path, float]] = []

    def snap(self, seconds: float) -> None:
        path = self.folder / f"{len(self.items):03d}.png"
        self.page.screenshot(path=path)
        self.items.append((path, seconds))

    def scroll(self, pixels: int, steps: int = 3, seconds: float = 0.35) -> None:
        # Over the margin of the page, not over a table that would scroll itself.
        self.page.mouse.move(345, 600)
        for _ in range(steps):
            self.page.mouse.wheel(0, pixels / steps)
            self.page.wait_for_timeout(350)
            self.snap(seconds)


def review_frames(page: Page, base: str, folder: Path) -> Frames:
    """The review of the alert with the highest priority, from the overview to
    the decision log."""
    frames = Frames(page, folder)
    open_page(page, base, "")
    frames.snap(2.5)
    frames.scroll(560)
    frames.snap(1.5)
    open_page(page, base, "alerts")
    frames.snap(2.0)
    select_first_row(page)
    frames.snap(1.5)
    frames.scroll(520)
    frames.snap(3.0)
    frames.scroll(360)
    # A reviewer documents why the indicator does not call for an action.
    page.get_by_text("No action: explained", exact=True).click()
    page.get_by_label("Reason").fill(
        "National framework agreements with highly specialised lots: few able suppliers"
    )
    page.get_by_label("Reviewer").fill("Demo reviewer")
    page.wait_for_timeout(500)
    frames.snap(1.5)
    page.get_by_role("button", name="Record the decision").click()
    settle(page, 1500)
    frames.snap(2.0)
    # Through the navigation: reloading the page would start a new session,
    # with an empty decision log in the public demo.
    page.get_by_role("link", name="Decision log").click()
    settle(page)
    frames.snap(3.0)
    return frames


def to_gif(frames: Frames, gif: Path, width: int = 1100) -> None:
    """Assemble the frames with ffmpeg, through one palette for the whole GIF."""
    playlist = frames.folder / "frames.txt"
    lines = []
    for path, seconds in frames.items:
        lines += [f"file '{path.as_posix()}'", f"duration {seconds}"]
    lines.append(f"file '{frames.items[-1][0].as_posix()}'")
    playlist.write_text("\n".join(lines) + "\n", encoding="utf-8")
    graph = (
        f"fps=10,scale={width}:-1:flags=lanczos,split[a][b];"
        "[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=5"
    )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(playlist),
            "-vf",
            graph,
            "-loop",
            "0",
            str(gif),
        ],
        check=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "docs" / "images")
    parser.add_argument("--no-gif", action="store_true")
    parser.add_argument("--chromium", help="path of a Chromium executable")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    port = free_port()
    env = {**os.environ, "PUBLIC_DEMO": "true", "GEMINI_API_KEY": ""}
    server = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            str(ROOT / "app" / "streamlit_app.py"),
            "--server.port",
            str(port),
            "--server.headless",
            "true",
        ],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        wait_for(f"{base}/_stcore/health")
        with sync_playwright() as playwright:
            launch = {"executable_path": args.chromium} if args.chromium else {}
            browser = playwright.chromium.launch(**launch)
            page = browser.new_page(viewport=VIEWPORT)
            screenshots(page, base, args.output)
            page.close()
            if not args.no_gif and shutil.which("ffmpeg"):
                page = browser.new_page(viewport=VIEWPORT)
                with tempfile.TemporaryDirectory() as tmp:
                    to_gif(review_frames(page, base, Path(tmp)), args.output / "demo.gif")
                print("demo.gif", flush=True)
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=30)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
