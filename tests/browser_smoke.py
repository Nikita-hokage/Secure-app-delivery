"""Exercise the dashboard with Chrome, a local WSGI server and Playwright."""

import shutil
import sys
from pathlib import Path
from threading import Thread
from wsgiref.simple_server import make_server

from playwright.sync_api import expect, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from app import app


def exercise(page, origin):
    errors = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.goto(origin)
    expect(page.locator("#score")).to_have_text("27 / 27")
    expect(page.locator("#rows tr")).to_have_count(9)
    page.select_option("#scenario", "peer-rewrite")
    expect(page.locator("#score")).to_have_text("25 / 27")
    page.check("#only-failures")
    expect(page.locator("#rows tr")).to_have_count(1)
    expect(page.locator("#findings li")).to_have_count(2)
    page.reload()
    expect(page.locator("#scenario")).to_have_value("peer-rewrite")
    page.set_viewport_size({"width": 375, "height": 800})
    assert page.evaluate("document.documentElement.scrollWidth === window.innerWidth")
    page.select_option("#scenario", "retry-drift")
    expect(page.locator("#score")).to_have_text("21 / 27")
    page.check("#only-failures")
    expect(page.locator("#rows tr")).to_have_count(3)
    page.select_option("#scenario", "clean")
    expect(page.locator("#empty")).to_be_visible()
    assert not errors, errors


if __name__ == "__main__":
    with make_server("127.0.0.1", 0, app) as server, sync_playwright() as playwright:
        Thread(target=server.serve_forever, daemon=True).start()
        try:
            chrome = shutil.which("google-chrome") or shutil.which("chromium")
            browser = playwright.chromium.launch(executable_path=chrome)
            page = browser.new_page(viewport={"width": 1100, "height": 850})
            exercise(page, f"http://127.0.0.1:{server.server_port}/")
            browser.close()
        finally:
            server.shutdown()
