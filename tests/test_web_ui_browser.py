from __future__ import annotations

import ipaddress
import socket
import struct
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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


class BrowserTestHTTPServer(ThreadingHTTPServer):
    daemon_threads = False
    block_on_close = True


def _browser_context_options(proxy_url: str) -> dict[str, object]:
    return {
        "service_workers": "block",
        "proxy": {
            "server": proxy_url,
            "bypass": "127.0.0.1,localhost,[::1]",
        },
    }


def _non_loopback_ipv4() -> str:
    addresses = {
        item[4][0]
        for item in socket.getaddrinfo(
            socket.gethostname(), 0, socket.AF_INET, socket.SOCK_DGRAM
        )
    }
    private_addresses = sorted(
        address
        for address in addresses
        if ipaddress.ip_address(address).is_private
        and not ipaddress.ip_address(address).is_loopback
    )
    if not private_addresses:
        pytest.fail("a non-loopback private IPv4 interface is required")
    return private_addresses[0]


def _serve_local_stun(
    listener: socket.socket,
    received: threading.Event,
    stop: threading.Event,
) -> None:
    listener.settimeout(0.1)
    magic_cookie = 0x2112A442
    cookie_bytes = struct.pack("!I", magic_cookie)
    while not stop.is_set():
        try:
            request, address = listener.recvfrom(2048)
        except TimeoutError:
            continue
        except OSError:
            return
        if len(request) < 20 or request[4:8] != cookie_bytes:
            continue
        received.set()
        client_ip, client_port = address[:2]
        xor_port = client_port ^ (magic_cookie >> 16)
        xor_address = bytes(
            left ^ right
            for left, right in zip(
                socket.inet_aton(client_ip), cookie_bytes, strict=True
            )
        )
        xor_mapped_address = (
            struct.pack("!HHBBH", 0x0020, 8, 0, 1, xor_port) + xor_address
        )
        response = (
            struct.pack("!HHI", 0x0101, len(xor_mapped_address), magic_cookie)
            + request[8:20]
            + xor_mapped_address
        )
        listener.sendto(response, address)


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
    server = BrowserTestHTTPServer(("127.0.0.1", 0), make_handler(tmp_path))
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    page_errors: list[str] = []
    console_errors: list[str] = []
    expected_console_errors: list[str] = []
    proxy_guard: socket.socket | None = None
    try:
        proxy_guard = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        proxy_guard.bind(("127.0.0.1", 0))
        proxy_url = f"http://127.0.0.1:{proxy_guard.getsockname()[1]}"
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True,
                args=["--force-webrtc-ip-handling-policy=disable_non_proxied_udp"],
            )
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
                setattr(
                    page,
                    "expect_console_error",
                    expected_console_errors.append,
                )
                page.on("pageerror", lambda error: page_errors.append(str(error)))

                def capture_console_error(message):
                    if message.type != "error":
                        return
                    if message.text in expected_console_errors:
                        expected_console_errors.remove(message.text)
                        return
                    console_errors.append(message.text)

                page.on("console", capture_console_error)
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
        try:
            assert not thread.is_alive()
            assert not any(
                request_thread.is_alive()
                for request_thread in getattr(server, "_threads", ())
            )
        finally:
            app.storage.close()
    assert page_errors == []
    assert console_errors == []
    assert expected_console_errors == []


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


def test_browser_blocks_non_proxied_webrtc_udp(browser_app):
    page, base_url, _ = browser_app
    interface = _non_loopback_ipv4()
    listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    listener.bind((interface, 0))
    received = threading.Event()
    stop = threading.Event()
    responder = threading.Thread(
        target=_serve_local_stun,
        args=(listener, received, stop),
    )
    responder.start()
    try:
        page.goto(base_url, wait_until="domcontentloaded")
        expect(page.locator("#jobs-body tr")).to_contain_text("Browser Fixture Job")
        page.evaluate(
            """
            async ({ host, port }) => {
              const connection = new RTCPeerConnection({
                iceServers: [{ urls: `stun:${host}:${port}` }],
              });
              try {
                connection.createDataChannel("egress-probe");
                const gatheringComplete = new Promise((resolve, reject) => {
                  const timeout = window.setTimeout(
                    () => reject(new Error("ICE gathering did not complete")),
                    5000,
                  );
                  connection.addEventListener("icegatheringstatechange", () => {
                    if (connection.iceGatheringState === "complete") {
                      window.clearTimeout(timeout);
                      resolve();
                    }
                  });
                });
                await connection.setLocalDescription(await connection.createOffer());
                if (connection.iceGatheringState !== "complete") {
                  await gatheringComplete;
                }
              } finally {
                connection.close();
              }
            }
            """,
            {"host": interface, "port": listener.getsockname()[1]},
        )
    finally:
        stop.set()
        listener.close()
        responder.join(timeout=2)

    assert not responder.is_alive()
    assert received.is_set() is False


def test_browser_server_close_drains_blocked_handlers_before_storage_close():
    handler_started = threading.Event()
    release_handler = threading.Event()
    handler_finished = threading.Event()
    storage_closed = threading.Event()
    client_errors: list[str] = []

    class BlockingHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            handler_started.set()
            release_handler.wait(timeout=5)
            handler_finished.set()
            self.send_response(204)
            self.end_headers()

        def log_message(self, format, *args):
            return

    server_class = globals().get("BrowserTestHTTPServer", ThreadingHTTPServer)
    server = server_class(("127.0.0.1", 0), BlockingHandler)
    serve_thread = threading.Thread(target=server.serve_forever)
    serve_thread.start()

    def request_blocked_handler():
        try:
            with socket.create_connection(server.server_address, timeout=2) as client:
                client.sendall(
                    b"GET /blocked HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n"
                )
                client.recv(1024)
        except OSError as exc:
            client_errors.append(str(exc))

    client_thread = threading.Thread(target=request_blocked_handler)
    client_thread.start()
    closer_thread: threading.Thread | None = None
    storage_closed_while_handler_blocked = False
    try:
        assert handler_started.wait(timeout=2)
        server.shutdown()
        serve_thread.join(timeout=2)
        assert not serve_thread.is_alive()

        def close_server_then_storage():
            server.server_close()
            storage_closed.set()

        closer_thread = threading.Thread(target=close_server_then_storage)
        closer_thread.start()
        storage_closed_while_handler_blocked = storage_closed.wait(timeout=0.2)
    finally:
        release_handler.set()
        if serve_thread.is_alive():
            server.shutdown()
            serve_thread.join(timeout=2)
        if closer_thread is None:
            server.server_close()
        else:
            closer_thread.join(timeout=2)
        client_thread.join(timeout=2)

    assert storage_closed_while_handler_blocked is False
    assert storage_closed.is_set()
    assert handler_finished.is_set()
    assert closer_thread is not None and not closer_thread.is_alive()
    assert not client_thread.is_alive()
    assert client_errors == []


def test_applied_status_records_application_and_learning_event(browser_app):
    page, base_url, app = browser_app
    job = app.storage.list_jobs(limit=1)[0]
    page.goto(base_url, wait_until="domcontentloaded")
    job_row = page.locator("#jobs-body tr").filter(has_text="Browser Fixture Job")
    job_row.click()
    expect(page.locator("#job-detail h2")).to_have_text("Browser Fixture Job")

    with page.expect_response(
        lambda response: urlsplit(response.url).path == "/api/jobs"
    ):
        page.locator("#job-detail").get_by_role("button", name="Откликнулся").click()

    application = app.storage.get_application(job.id)
    assert application is not None
    assert application.status == "applied"
    behavior = app.storage.get_behavior_stats()
    assert behavior["by_action"]["applied"] == 1
    expect(job_row.locator("td").nth(4)).to_have_text("applied")


def test_selected_status_refreshes_detail_for_explicit_job_id(browser_app):
    page, base_url, app = browser_app
    job = app.storage.list_jobs(limit=1)[0]
    page.goto(base_url, wait_until="domcontentloaded")
    job_row = page.locator("#jobs-body tr").filter(has_text="Browser Fixture Job")
    job_row.click()
    expect(page.locator("#job-detail h2")).to_have_text("Browser Fixture Job")

    with page.expect_response(
        lambda response: urlsplit(response.url).path == f"/api/jobs/{job.id}",
        timeout=3000,
    ) as detail_response:
        page.locator("#job-detail").get_by_role("button", name="Сохранить").click()

    assert detail_response.value.request.method == "GET"
    assert page.evaluate("state.selectedId") == job.id
    expect(job_row.locator("td").nth(4)).to_have_text("saved")


def test_resume_edit_populates_fields_and_preserves_active(browser_app):
    page, base_url, app = browser_app
    resume_id = app.storage.save_resume(
        Resume(
            name="Backend CV",
            body="Python and PostgreSQL",
            profile_id="default",
            is_active=True,
            ats_score=87,
        )
    )
    page.goto(f"{base_url}/settings", wait_until="domcontentloaded")
    resume_row = page.locator("#resumes-list .source-row").filter(has_text="Backend CV")
    resume_row.get_by_text("Ред.", exact=True).click()
    expect(page.locator("#resume-name-input")).to_have_value("Backend CV")
    expect(page.locator("#resume-body-input")).to_have_value("Python and PostgreSQL")
    with page.expect_response(lambda response: response.url.endswith("/api/resumes")):
        page.locator("#save-resume-button").click()

    saved = app.storage.get_resume(resume_id)
    assert saved.name == "Backend CV"
    assert saved.body == "Python and PostgreSQL"
    assert saved.profile_id == "default"
    assert saved.is_active is True
    assert saved.ats_score == 87


def test_stale_resume_edit_returns_not_found_and_keeps_draft(browser_app):
    page, base_url, app = browser_app
    resume_id = app.storage.save_resume(
        Resume(
            name="Stale CV",
            body="Original body",
            profile_id="default",
            is_active=True,
            ats_score=64,
        )
    )
    page.on("dialog", lambda dialog: dialog.dismiss())
    page.goto(f"{base_url}/settings", wait_until="domcontentloaded")
    resume_row = page.locator("#resumes-list .source-row").filter(has_text="Stale CV")
    resume_row.get_by_text("Ред.", exact=True).click()
    app.storage.delete_resume(resume_id)
    page.locator("#resume-body-input").fill("Unsaved draft")
    page.expect_console_error(
        "Failed to load resource: the server responded with a status of 404 (Not Found)"
    )

    with page.expect_response(
        lambda response: (
            response.url.endswith("/api/resumes") and response.request.method == "POST"
        )
    ) as response_info:
        page.locator("#save-resume-button").click()

    assert response_info.value.status == 404
    assert response_info.value.json() == {"error": "not_found"}
    expect(page.locator("#resume-form")).to_be_visible()
    expect(page.locator("#resume-name-input")).to_have_value("Stale CV")
    expect(page.locator("#resume-body-input")).to_have_value("Unsaved draft")
    expect(page.locator("#save-resume-button")).to_have_text("Обновить")
    assert page.evaluate("editingResumeId") == resume_id
    assert app.storage.get_resume(resume_id) is None


def test_resume_and_ghost_actions_expose_record_context(browser_app):
    page, base_url, app = browser_app
    app.storage.save_resume(
        Resume(
            name="Accessible CV",
            body="Body",
            profile_id="default",
        )
    )
    page.route(
        "**/api/ghost-jobs?days=7",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body='[{"id":22,"title":"Backend B","company":"Two","source":"x"}]',
        ),
    )
    page.goto(f"{base_url}/settings", wait_until="domcontentloaded")

    edit_action = page.get_by_role(
        "button", name="Редактировать резюме Accessible CV", exact=True
    )
    expect(edit_action).to_be_visible()
    edit_action.click()
    expect(page.locator("#save-resume-button")).to_have_text("Обновить")

    page.locator("#check-ghost-button").click()
    expect(
        page.get_by_role("button", name="Отметить ghosted: Backend B", exact=True)
    ).to_be_visible()


def test_ghost_action_updates_rendered_job_not_selected_job(browser_app):
    page, base_url, _ = browser_app
    request_urls: list[str] = []
    page.on("request", lambda request: request_urls.append(request.url))
    page.route(
        "**/api/ghost-jobs?days=7",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body='[{"id":11,"title":"A","company":"One","source":"x"},'
            '{"id":22,"title":"B","company":"Two","source":"x"}]',
        ),
    )

    def complete_job_action(route):
        route.fulfill(
            status=200, content_type="application/json", body='{"status":"ok"}'
        )

    page.route("**/api/jobs/*/status", complete_job_action)
    page.route("**/api/jobs/*/record-event", complete_job_action)
    page.goto(f"{base_url}/settings", wait_until="domcontentloaded")
    page.evaluate("state.selectedId = 11")
    page.locator("#check-ghost-button").click()
    ghost_action = (
        page.locator("#ghost-jobs-list .source-row")
        .nth(1)
        .get_by_text("Отметить ghosted", exact=True)
    )
    expect(ghost_action).to_be_visible()
    with (
        page.expect_response(
            lambda response: urlsplit(response.url).path == "/api/jobs", timeout=3000
        ),
        page.expect_response(
            lambda response: response.url.endswith("/api/ghost-jobs?days=7")
        ),
    ):
        ghost_action.click()

    assert any(url.endswith("/api/jobs/22/status") for url in request_urls)
    assert any(url.endswith("/api/jobs/22/record-event") for url in request_urls)
    assert not any(url.endswith("/api/jobs/11/status") for url in request_urls)
    assert not any(url.endswith("/api/jobs/11/record-event") for url in request_urls)
