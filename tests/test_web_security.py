from __future__ import annotations

import threading
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer

import pytest

from work_hunter.web.security import ensure_loopback_listener
from work_hunter.web.server import make_handler, run_server


@pytest.fixture
def cockpit(tmp_path):
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(tmp_path))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def raw_request(cockpit, method: str, path: str, headers: dict[str, str], body: bytes | None):
    connection = HTTPConnection("127.0.0.1", cockpit.server_port, timeout=5)
    try:
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        response_body = response.read().decode("utf-8")
        response_headers = {name.lower(): value for name, value in response.getheaders()}
        return response.status, response_headers, response_body
    finally:
        connection.close()


def test_non_loopback_bind_is_rejected_before_server_creation():
    with pytest.raises(ValueError, match="loopback"):
        ensure_loopback_listener("0.0.0.0")


def test_run_server_rejects_non_loopback_before_server_creation(monkeypatch, tmp_path):
    constructed = False

    def fail_constructor(*args, **kwargs):
        nonlocal constructed
        constructed = True
        raise AssertionError("HTTP server must not be constructed")

    monkeypatch.setattr("work_hunter.web.server.ThreadingHTTPServer", fail_constructor)
    with pytest.raises(ValueError, match="loopback"):
        run_server(tmp_path, host="0.0.0.0")
    assert constructed is False


@pytest.mark.parametrize(
    ("headers", "status"),
    [
        ({"Host": "evil.test", "Content-Type": "application/json"}, 403),
        ({"Origin": "https://evil.test", "Content-Type": "application/json"}, 403),
        ({"Content-Type": "text/plain"}, 415),
    ],
)
def test_mutation_boundary_rejects_unsafe_request_before_service(
    cockpit, monkeypatch, headers, status
):
    constructed = False

    def fail_constructor(*args, **kwargs):
        nonlocal constructed
        constructed = True
        raise AssertionError("WorkHunter must not be constructed")

    monkeypatch.setattr("work_hunter.web.server.WorkHunter", fail_constructor)
    code, _, _ = raw_request(cockpit, "POST", "/api/score", headers, b"{}")
    assert code == status
    assert constructed is False


def test_same_origin_json_and_originless_local_script_are_accepted(cockpit):
    host = f"127.0.0.1:{cockpit.server_port}"
    same_origin = {"Host": host, "Origin": f"http://{host}", "Content-Type": "application/json"}
    assert raw_request(cockpit, "POST", "/api/score", same_origin, b"{}")[0] == 200
    assert raw_request(
        cockpit,
        "POST",
        "/api/score",
        {"Host": host, "Content-Type": "application/json"},
        b"{}",
    )[0] == 200


def test_all_responses_include_security_headers(cockpit):
    code, headers, _ = raw_request(cockpit, "GET", "/", {}, None)
    assert code == 200
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
    assert headers["referrer-policy"] == "no-referrer"


def test_options_is_rejected_without_cors_authorization_headers(cockpit):
    code, headers, _ = raw_request(cockpit, "OPTIONS", "/api/score", {}, None)
    assert code == 405
    assert "access-control-allow-origin" not in headers
    assert "access-control-allow-methods" not in headers
    assert "access-control-allow-headers" not in headers
