from __future__ import annotations

import socket
import threading
from http.server import ThreadingHTTPServer
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import expect, sync_playwright

from work_hunter.models import Job, Resume
from work_hunter.services import WorkHunter
from work_hunter.web.server import make_handler


REMOTE_ASSET_STUBS = {
    "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap": "text/css",
    "https://unpkg.com/lucide@latest/dist/umd/lucide.js": "application/javascript",
}


def _browser_context_options(proxy_url: str) -> dict[str, object]:
    return {
        "service_workers": "block",
        "proxy": {
            "server": proxy_url,
            "bypass": "127.0.0.1,localhost,[::1]",
        },
    }


@pytest.fixture
def browser_app(tmp_path):
    app = WorkHunter(tmp_path)
    app.storage.upsert_job(
        Job(
            source="browser-fixture",
            source_id="smoke-job",
            url="https://example.test/smoke-job",
            title="Browser Fixture Job",
            company="Local Test",
        )
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(tmp_path))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    page_errors: list[str] = []
    console_errors: list[str] = []
    proxy_guard: socket.socket | None = None
    try:
        proxy_guard = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        proxy_guard.bind(("127.0.0.1", 0))
        proxy_url = f"http://127.0.0.1:{proxy_guard.getsockname()[1]}"
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                context = browser.new_context(**_browser_context_options(proxy_url))

                def keep_browser_requests_local(route):
                    url = route.request.url
                    if url in REMOTE_ASSET_STUBS:
                        route.fulfill(
                            status=200,
                            content_type=REMOTE_ASSET_STUBS[url],
                            body="",
                        )
                        return
                    hostname = urlsplit(url).hostname
                    if hostname in {
                        "127.0.0.1",
                        "localhost",
                        "::1",
                    } or url.startswith(("about:", "blob:", "data:")):
                        route.continue_()
                        return
                    route.abort("blockedbyclient")

                context.route("**/*", keep_browser_requests_local)
                page = context.new_page()
                page.on("pageerror", lambda error: page_errors.append(str(error)))
                page.on(
                    "console",
                    lambda message: (
                        console_errors.append(message.text)
                        if message.type == "error"
                        else None
                    ),
                )
                try:
                    yield page, base_url, app
                finally:
                    context.close()
            finally:
                browser.close()
    finally:
        if proxy_guard is not None:
            proxy_guard.close()
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        app.storage.close()
    assert not thread.is_alive()
    assert page_errors == []
    assert console_errors == []


def test_ui_loads_without_browser_errors(browser_app):
    page, base_url, _ = browser_app
    page.route(
        f"{base_url}/__background-readiness",
        lambda route: route.fulfill(status=204, body=""),
    )
    page.add_init_script(
        """
        window.addEventListener("DOMContentLoaded", () => {
          window.__readinessInterval = window.setInterval(() => {
            fetch("/__background-readiness").catch(() => {});
          }, 100);
        }, { once: true });
        """
    )

    try:
        page.goto(base_url, wait_until="domcontentloaded")
        rows = page.locator("#jobs-body tr")
        expect(rows).to_have_count(1)
        expect(rows).to_contain_text("Browser Fixture Job")
        expect(page.locator("body")).not_to_have_attribute("aria-busy", "true")
    finally:
        page.evaluate("window.clearInterval(window.__readinessInterval)")


def test_browser_context_configuration_is_fail_closed():
    assert _browser_context_options("http://127.0.0.1:43123") == {
        "service_workers": "block",
        "proxy": {
            "server": "http://127.0.0.1:43123",
            "bypass": "127.0.0.1,localhost,[::1]",
        },
    }


def test_browser_context_blocks_service_workers(browser_app):
    page, base_url, _ = browser_app
    service_worker_requests: list[str] = []
    page.context.on(
        "request",
        lambda request: (
            service_worker_requests.append(request.url)
            if request.url.endswith("/sw.js")
            else None
        ),
    )

    page.goto(base_url, wait_until="domcontentloaded")
    expect(page.locator("#jobs-body tr")).to_contain_text("Browser Fixture Job")

    assert service_worker_requests == []


def test_resume_edit_populates_fields_and_preserves_active(browser_app):
    page, base_url, app = browser_app
    resume_id = app.storage.save_resume(
        Resume(
            name="Backend CV",
            body="Python and PostgreSQL",
            profile_id="default",
            is_active=True,
        )
    )
    page.goto(f"{base_url}/settings", wait_until="networkidle")
    page.get_by_role("button", name="Ред.").click()
    expect(page.locator("#resume-name-input")).to_have_value("Backend CV")
    expect(page.locator("#resume-body-input")).to_have_value("Python and PostgreSQL")
    with page.expect_response(lambda response: response.url.endswith("/api/resumes")):
        page.locator("#save-resume-button").click()

    saved = app.storage.get_resume(resume_id)
    assert saved.name == "Backend CV"
    assert saved.body == "Python and PostgreSQL"
    assert saved.is_active is True


def test_ghost_action_updates_rendered_job_not_selected_job(browser_app):
    page, base_url, _ = browser_app
    status_urls: list[str] = []
    page.route(
        "**/api/ghost-jobs?days=7",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body='[{"id":11,"title":"A","company":"One","source":"x"},'
            '{"id":22,"title":"B","company":"Two","source":"x"}]',
        ),
    )

    def capture_status(route):
        status_urls.append(route.request.url)
        route.fulfill(
            status=200, content_type="application/json", body='{"status":"ok"}'
        )

    page.route("**/api/jobs/*/status", capture_status)
    page.goto(f"{base_url}/settings", wait_until="networkidle")
    page.evaluate("state.selectedId = 11")
    page.locator("#check-ghost-button").click()
    page.locator("#ghost-jobs-list .source-row").nth(1).get_by_role(
        "button", name="Отметить ghosted"
    ).click()
    page.wait_for_timeout(100)
    assert any(url.endswith("/api/jobs/22/status") for url in status_urls)
    assert not any(url.endswith("/api/jobs/11/status") for url in status_urls)
