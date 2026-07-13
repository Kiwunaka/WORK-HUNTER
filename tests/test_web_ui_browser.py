from __future__ import annotations

import ipaddress
import json
import re
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
    request_urls: list[str] = []
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
                context.on("request", lambda request: request_urls.append(request.url))
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
    assert all(
        urlsplit(url).hostname in {None, "127.0.0.1", "localhost", "::1"}
        or url.startswith("data:")
        for url in request_urls
    ), request_urls


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


@pytest.mark.parametrize(
    ("incoming", "canonical"),
    [
        ("/", "/today"),
        ("/favorites?filter=all&x=1#saved", "/jobs?filter=saved&x=1#saved"),
        ("/chat?job=7", "/assistant?job=7"),
        ("/agent", "/applications?tab=agent"),
        ("/stats", "/analytics?tab=overview"),
        ("/trends", "/analytics?tab=trends"),
        ("/jobs?filter=all&min_score=070", "/jobs?filter=all"),
    ],
)
def test_ui_routes_canonicalize_legacy_paths(browser_app, incoming, canonical):
    page, base_url, _ = browser_app

    page.goto(base_url + incoming, wait_until="domcontentloaded")

    expect(page).to_have_url(base_url + canonical)


def test_route_resolver_uses_first_recognized_value_and_loaded_source_keys(browser_app):
    page, base_url, _ = browser_app
    page.goto(base_url + "/today", wait_until="domcontentloaded")

    valid = page.evaluate(
        """() => WorkHunterUI.route.resolve(
          "/jobs", "?filter=saved&filter=all&source=hh&x=1", "", ["hh"]
        )"""
    )
    invalid = page.evaluate(
        """() => WorkHunterUI.route.resolve(
          "/jobs", "?filter=all&source=missing", "", ["hh"]
        )"""
    )

    assert valid["canonicalUrl"] == "/jobs?filter=saved&source=hh&x=1"
    assert invalid["canonicalUrl"] == "/jobs?filter=all"
    assert invalid["warnings"] == ["invalid_source"]


def test_popstate_reapplies_canonical_route_without_extra_history(browser_app):
    page, base_url, _ = browser_app
    page.goto(base_url + "/today", wait_until="domcontentloaded")
    initial_length = page.evaluate("history.length")

    page.evaluate(
        """() => {
          history.pushState({}, "", "/stats");
          window.dispatchEvent(new PopStateEvent("popstate"));
        }"""
    )

    expect(page).to_have_url(base_url + "/analytics?tab=overview")
    assert page.evaluate("history.length") == initial_length + 1


def test_static_and_dynamic_controls_work_without_inline_handlers(browser_app):
    page, base_url, app = browser_app
    job = app.storage.list_jobs(limit=1)[0]
    page.goto(f"{base_url}/calendar", wait_until="domcontentloaded")

    expect(page.locator("[onclick]")).to_have_count(0)

    page.get_by_role("button", name="+ Событие", exact=True).click()
    expect(page.locator("#event-form")).to_be_visible()
    page.locator("#event-form").get_by_role("button", name="Отмена", exact=True).click()
    expect(page.locator("#event-form")).to_be_hidden()

    page.goto(base_url, wait_until="domcontentloaded")
    page.locator("#jobs-body tr").filter(has_text="Browser Fixture Job").click()
    expect(page.locator("#job-detail h2")).to_have_text("Browser Fixture Job")
    expect(page.locator("#job-detail [onclick]")).to_have_count(0)
    with page.expect_response(
        lambda response: urlsplit(response.url).path == "/api/jobs"
    ):
        page.locator("#job-detail").get_by_role(
            "button", name="Скрыть", exact=True
        ).click()
    assert app.storage.get_job(job.id).status == "hidden"


def test_untrusted_api_text_cannot_create_markup_or_handlers(browser_app):
    page, base_url, _ = browser_app
    malicious = '<img id="pwned" src=x onerror="window.__pwned=1">'
    page.route(
        "**/api/jobs?*",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                [
                    {
                        "id": 1,
                        "title": "Safe title",
                        "company": "Company",
                        "source": malicious,
                        "status": "new",
                        "score": None,
                    }
                ]
            ),
        ),
    )
    page.goto(base_url, wait_until="networkidle")
    expect(page.locator("#jobs-body")).to_contain_text(malicious)
    expect(page.locator("#pwned")).to_have_count(0)
    assert page.evaluate("window.__pwned") is None


def test_untrusted_stats_values_cannot_create_markup_or_handlers(browser_app):
    page, base_url, _ = browser_app
    malicious = '<img id="pwned" src=x onerror="window.__pwned=1">'
    page.route(
        "**/api/stats",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "total_jobs": 1,
                    "avg_score": 10,
                    "high_score": 0,
                    "total_applications": 0,
                    "by_source": {malicious: malicious},
                    "score_distribution": {malicious: malicious},
                    "application_funnel": [{"status": malicious, "count": malicious}],
                }
            ),
        ),
    )
    page.goto(f"{base_url}/stats", wait_until="networkidle")
    expect(page.locator("#view-stats")).to_contain_text(malicious)
    expect(page.locator("#pwned")).to_have_count(0)
    assert page.evaluate("window.__pwned") is None


def test_untrusted_record_id_cannot_create_markup_or_handlers(browser_app):
    page, base_url, _ = browser_app
    malicious_id = '1"><img id="pwned" src=x onerror="window.__pwned=1">'
    page.route(
        "**/api/jobs?*",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                [
                    {
                        "id": malicious_id,
                        "title": "Unsafe identifier",
                        "company": "Company",
                        "source": "source",
                        "status": "new",
                        "score": None,
                    }
                ]
            ),
        ),
    )
    page.goto(base_url, wait_until="networkidle")
    expect(page.locator("#pwned")).to_have_count(0)
    assert page.evaluate("window.__pwned") is None


def test_untrusted_action_key_cannot_create_inline_handlers(browser_app):
    page, base_url, _ = browser_app
    malicious = 'safe" onmouseover="window.__pwned=1" data-x="'
    page.route(
        "**/api/templates",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps([{"name": malicious, "body": "Template body"}]),
        ),
    )
    page.goto(f"{base_url}/agent", wait_until="networkidle")
    expect(page.locator("#agent-templates-list")).to_contain_text(malicious)
    expect(page.locator("#agent-templates-list [onmouseover]")).to_have_count(0)
    assert page.evaluate("window.__pwned") is None


def test_untrusted_job_url_is_not_an_executable_link(browser_app):
    page, base_url, _ = browser_app
    payload = {
        "id": 1,
        "title": "Unsafe URL",
        "company": "Company",
        "source": "source",
        "status": "new",
        "score": None,
        "url": "javascript:window.__pwned=1",
    }
    page.route(
        "**/api/jobs?*",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps([payload]),
        ),
    )
    page.route(
        "**/api/jobs/1",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(payload),
        ),
    )
    page.goto(base_url, wait_until="networkidle")
    page.locator("#jobs-body tr").click()
    link = page.locator("#job-detail a")
    expect(link).not_to_have_attribute("href", re.compile(r"^javascript:", re.I))
    assert page.evaluate("window.__pwned") is None


def test_untrusted_api_error_is_rendered_as_text(browser_app):
    page, base_url, _ = browser_app
    malicious = '<img id="pwned" src=x onerror="window.__pwned=1">'
    page.route(
        "**/api/market-trends",
        lambda route: route.fulfill(
            status=400,
            content_type="application/json",
            body=json.dumps({"error": malicious}),
        ),
    )
    page.goto(f"{base_url}/trends", wait_until="networkidle")
    page.expect_console_error(
        "Failed to load resource: the server responded with a status of 400 (Bad Request)"
    )
    page.locator("#load-trends-button").click()
    expect(page.locator("#trends-output")).to_contain_text(malicious)
    expect(page.locator("#pwned")).to_have_count(0)
    assert page.evaluate("window.__pwned") is None


def test_job_row_opens_with_enter_and_checkbox_space_stays_independent(browser_app):
    page, base_url, app = browser_app
    app.storage.upsert_job(
        Job(source="x", source_id="keyboard", url="https://x/keyboard", title="Python")
    )
    page.goto(base_url, wait_until="networkidle")
    row = page.locator("#jobs-body tr").filter(has_text="Python")
    row.focus()
    page.keyboard.press("Enter")
    expect(page.locator("#job-detail h2")).to_contain_text("Python")
    page.locator("#job-detail").evaluate("element => element.innerHTML = ''")
    checkbox = row.locator("input[type=checkbox]")
    checkbox.focus()
    page.keyboard.press("Space")
    expect(page.locator("#job-detail h2")).to_have_count(0)


def test_dynamic_busy_button_restores_label_on_success_and_error(browser_app):
    page, base_url, app = browser_app
    app.storage.upsert_job(
        Job(source="x", source_id="busy", url="https://x/busy", title="Busy")
    )
    calls = 0

    def result(route):
        nonlocal calls
        calls += 1
        if calls == 1:
            route.fulfill(
                status=200, content_type="application/json", body='{"ok":true}'
            )
        else:
            route.fulfill(status=200, content_type="application/json", body="{invalid")

    page.route("**/parse-structure", result)
    page.on("dialog", lambda dialog: dialog.dismiss())
    page.goto(base_url, wait_until="networkidle")
    page.locator("#jobs-body tr").filter(has_text="Busy").click()
    button = page.get_by_role("button", name="Структура")
    original = button.text_content()
    button.click()
    expect(button).to_have_text(original)
    button.click()
    expect(button).to_have_text(original)


def test_overlapping_busy_owners_require_matching_releases_and_restore_empty_label(
    browser_app,
):
    page, base_url, _ = browser_app
    page.goto(base_url, wait_until="networkidle")

    states = page.evaluate(
        """
        async () => {
          const button = document.createElement("button");
          button.textContent = "";
          document.body.appendChild(button);
          const snapshot = () => ({ disabled: button.disabled, label: button.textContent });
          const releases = [];
          const run = (label) => {
            setBusy(button, true, label);
            return new Promise((resolve) => releases.push(resolve))
              .finally(() => setBusy(button, false));
          };

          const first = run("Первый");
          const second = run("Второй");
          const afterTwoStarts = snapshot();
          releases[0]();
          await first;
          const afterOneFinish = snapshot();
          releases[1]();
          await second;
          const afterTwoFinishes = snapshot();
          setBusy(button, false);
          setBusy(button, true, "Новый");
          const afterRestart = snapshot();
          setBusy(button, false);
          const afterRestartFinish = snapshot();
          button.remove();

          return {
            afterTwoStarts,
            afterOneFinish,
            afterTwoFinishes,
            afterRestart,
            afterRestartFinish,
          };
        }
        """
    )

    assert states == {
        "afterTwoStarts": {"disabled": True, "label": "Второй"},
        "afterOneFinish": {"disabled": True, "label": "Второй"},
        "afterTwoFinishes": {"disabled": False, "label": ""},
        "afterRestart": {"disabled": True, "label": "Новый"},
        "afterRestartFinish": {"disabled": False, "label": ""},
    }


def test_initial_source_failure_does_not_abort_jobs(browser_app):
    page, base_url, app = browser_app
    app.storage.upsert_job(
        Job(
            source="x",
            source_id="still-loads",
            url="https://x/job",
            title="Still loads",
        )
    )
    page.route(
        "**/api/sources",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body="{invalid",
        ),
    )
    page.goto(base_url, wait_until="networkidle")
    expect(page.locator("#jobs-body")).to_contain_text("Still loads")
    expect(page.locator("#init-errors")).to_be_visible()
    retry = page.locator("#init-errors button")
    expect(retry).to_be_visible()
    page.unroute("**/api/sources")
    retry.click()
    expect(page.locator("#sources-list")).to_contain_text(
        "Источники еще не синхронизировались"
    )
    expect(page.locator("#init-errors")).to_be_hidden()


def test_initial_jobs_loading_exposes_and_restores_busy_state(browser_app):
    page, base_url, _ = browser_app
    page.add_init_script(
        """
        const nativeFetch = window.fetch.bind(window);
        let releaseJobs;
        const jobsGate = new Promise((resolve) => { releaseJobs = resolve; });
        window.__releaseJobs = releaseJobs;
        window.fetch = (...args) => {
          const requestUrl = String(args[0] instanceof Request ? args[0].url : args[0]);
          if (requestUrl.includes("/api/jobs?")) {
            return jobsGate.then(() => nativeFetch(...args));
          }
          return nativeFetch(...args);
        };
        """
    )
    page.goto(base_url, wait_until="domcontentloaded")
    job_list = page.locator("#jobs-body").locator("xpath=ancestor::section[1]")
    try:
        expect(job_list).to_have_attribute("aria-busy", "true")
        expect(page.locator("#jobs-skeleton")).to_be_visible()
    finally:
        page.evaluate("window.__releaseJobs()")
    expect(page.locator("#jobs-body")).to_contain_text("Browser Fixture Job")
    expect(job_list).to_have_attribute("aria-busy", "false")
    expect(page.locator("#jobs-skeleton")).to_be_hidden()


def test_overlapping_jobs_loads_stay_busy_until_every_request_settles(browser_app):
    page, base_url, _ = browser_app
    page.add_init_script(
        """
        const nativeFetch = window.fetch.bind(window);
        window.__jobLoadReleases = [];
        window.fetch = (...args) => {
          const requestUrl = String(args[0] instanceof Request ? args[0].url : args[0]);
          if (!requestUrl.includes("/api/jobs?")) return nativeFetch(...args);
          const gate = new Promise((resolve) => window.__jobLoadReleases.push(resolve));
          return gate.then(() => nativeFetch(...args));
        };
        """
    )
    page.goto(base_url, wait_until="domcontentloaded")
    page.wait_for_function("window.__jobLoadReleases.length === 1")
    job_list = page.locator("#jobs-body").locator("xpath=ancestor::section[1]")
    expect(job_list).to_have_attribute("aria-busy", "true")
    expect(page.locator("#jobs-skeleton")).to_be_visible()

    try:
        page.locator("#refresh-button").click()
        page.wait_for_function("window.__jobLoadReleases.length === 2")
        page.evaluate("window.__jobLoadReleases[0]()")
        expect(page.locator("#jobs-body")).to_contain_text("Browser Fixture Job")
        expect(job_list).to_have_attribute("aria-busy", "true")
        expect(page.locator("#jobs-skeleton")).to_be_visible()
    finally:
        page.evaluate("window.__jobLoadReleases.forEach((release) => release())")

    expect(job_list).to_have_attribute("aria-busy", "false")
    expect(page.locator("#jobs-skeleton")).to_be_hidden()
    page.evaluate(
        """
        setSectionLoading("jobs", false);
        setSectionLoading("jobs", true);
        """
    )
    expect(job_list).to_have_attribute("aria-busy", "true")
    expect(page.locator("#jobs-skeleton")).to_be_visible()
    page.evaluate("setSectionLoading('jobs', false)")
    expect(job_list).to_have_attribute("aria-busy", "false")
    expect(page.locator("#jobs-skeleton")).to_be_hidden()


def test_agent_live_auth_local_failure_is_visible_and_restores_button(browser_app):
    page, base_url, _ = browser_app
    page.route(
        "**/api/agent/preflight?live_auth=true",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body="{invalid",
        ),
    )
    page.goto(f"{base_url}/agent", wait_until="networkidle")
    button = page.locator("#agent-live-auth-button")
    original = button.text_content()
    button.click()
    expect(button).to_have_text(original)
    expect(page.locator("#init-errors")).to_contain_text("Проверка HH auth")


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


def test_agent_load_is_dry_until_explicit_live_auth_click(browser_app):
    page, base_url, _ = browser_app
    preflight_urls: list[str] = []
    page.on(
        "request",
        lambda request: (
            preflight_urls.append(request.url)
            if "/api/agent/preflight" in request.url
            else None
        ),
    )
    with page.expect_response(
        lambda response: (
            urlsplit(response.url).path == "/api/agent/preflight"
            and "live_auth=true" not in response.url
        )
    ):
        page.goto(f"{base_url}/agent", wait_until="domcontentloaded")
    assert preflight_urls
    assert all("live_auth=true" not in url for url in preflight_urls)
    with page.expect_request(
        lambda request: (
            urlsplit(request.url).path == "/api/agent/preflight"
            and "live_auth=true" in request.url
        )
    ):
        page.locator("#agent-live-auth-button").click()
    assert sum("live_auth=true" in url for url in preflight_urls) == 1
