"""Shared pytest fixtures.

Makes the ``scraper/`` and ``agent/`` packages importable (they're plain
folders, not packages), spins up a per-session headless Chromium, and
serves the HTML fixtures over a local HTTP server so Playwright can
load them without CDN access.
"""

from __future__ import annotations

import http.server
import os
import socketserver
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"

# Make sibling folders importable as modules.
sys.path.insert(0, str(ROOT / "scraper"))
sys.path.insert(0, str(ROOT / "agent"))


# Prefer the pre-installed sandbox Chromium if present. Users running the
# suite locally can either leave this env var unset (Playwright-managed
# browsers) or override PW_CHROMIUM_PATH.
DEFAULT_CHROMIUM = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"
CHROMIUM_PATH = os.environ.get("PW_CHROMIUM_PATH", DEFAULT_CHROMIUM)


def _launch_browser(p):
    """Launch Chromium, using a known executable path when available."""
    kwargs = {"headless": True}
    if CHROMIUM_PATH and Path(CHROMIUM_PATH).exists():
        kwargs["executable_path"] = CHROMIUM_PATH
    return p.chromium.launch(**kwargs)


@pytest.fixture(scope="session")
def playwright_instance():
    """Session-scoped Playwright sync instance."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        yield p


@pytest.fixture(scope="session")
def browser(playwright_instance):
    """Session-scoped Chromium browser."""
    b = _launch_browser(playwright_instance)
    yield b
    b.close()


@pytest.fixture()
def page(browser):
    """Fresh page per test (new context so cookies don't leak)."""
    context = browser.new_context(locale="en-US")
    p = context.new_page()
    yield p
    context.close()


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    """SimpleHTTPRequestHandler that doesn't spam stderr with every GET."""

    def log_message(self, format, *args):
        return


@pytest.fixture(scope="session")
def fixture_server():
    """Serve tests/fixtures/ on a random port for the whole session."""
    os.chdir(FIXTURES)
    httpd = socketserver.TCPServer(("127.0.0.1", 0), _QuietHandler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture()
def tmp_output_dir(monkeypatch, tmp_path):
    """Redirect both modules' OUTPUT_DIR at a pytest tmp_path."""
    import google_maps_scraper as gms
    import lead_agent as agent_mod

    monkeypatch.setattr(gms, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(agent_mod, "OUTPUT_DIR", tmp_path)
    return tmp_path
