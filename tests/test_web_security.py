from __future__ import annotations

import json
import socket
import threading
import time
from http import HTTPStatus
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer

import pytest

from work_hunter.services import WorkHunter
from work_hunter.web import security as web_security
from work_hunter.web import server as web_server
from work_hunter.web.security import ensure_loopback_listener, request_boundary_error
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


def raw_request_with_header_pairs(
    cockpit,
    method: str,
    path: str,
    headers: list[tuple[str, str]],
    body: bytes | None,
):
    connection = HTTPConnection("127.0.0.1", cockpit.server_port, timeout=5)
    try:
        connection.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        for name, value in headers:
            connection.putheader(name, value)
        if body is not None:
            connection.putheader("Content-Length", str(len(body)))
        connection.endheaders(body)
        response = connection.getresponse()
        response_body = response.read().decode("utf-8")
        response_headers = {name.lower(): value for name, value in response.getheaders()}
        return response.status, response_headers, response_body
    finally:
        connection.close()


def assert_security_headers(headers: dict[str, str]) -> None:
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
    assert headers["referrer-policy"] == "no-referrer"


def boundary_error(
    host_header: str,
    origin_header: str | None,
    *,
    listener_port: int = 8787,
):
    return request_boundary_error(
        method="POST",
        host_header=host_header,
        origin_header=origin_header,
        content_type="application/json",
        listener_host="127.0.0.1",
        listener_port=listener_port,
    )


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
    ("host", "expected_bind_host"),
    [
        ("127.0.0.1", "127.0.0.1"),
        ("localhost", "127.0.0.1"),
    ],
)
def test_run_server_uses_verified_ipv4_loopback_endpoint(
    monkeypatch, tmp_path, host, expected_bind_host
):
    addresses = []

    def fake_getaddrinfo(*args, **kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0)),
        ]

    class FakeServer:
        def __init__(self, address, handler):
            addresses.append(address)

        def serve_forever(self):
            raise KeyboardInterrupt

        def server_close(self):
            return None

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr("work_hunter.web.server.ThreadingHTTPServer", FakeServer)
    run_server(tmp_path, host=host, port=0)
    assert addresses == [(expected_bind_host, 0)]


def test_ipv6_listener_is_rejected_by_ipv4_only_contract():
    with pytest.raises(ValueError, match="IPv4 loopback"):
        ensure_loopback_listener("::1")


def test_localhost_resolution_must_remain_on_ipv4_loopback(monkeypatch):
    def fake_getaddrinfo(*args, **kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("203.0.113.10", 0)),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(ValueError, match="IPv4 loopback"):
        ensure_loopback_listener("localhost")


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
    body_read = False

    def fail_constructor(*args, **kwargs):
        nonlocal constructed
        constructed = True
        raise AssertionError("WorkHunter must not be constructed")

    def fail_read_json(*args, **kwargs):
        nonlocal body_read
        body_read = True
        raise AssertionError("request body must not be parsed")

    monkeypatch.setattr("work_hunter.web.server.WorkHunter", fail_constructor)
    monkeypatch.setattr(cockpit.RequestHandlerClass, "_read_json", fail_read_json)
    code, response_headers, _ = raw_request(
        cockpit,
        "POST",
        "/api/score",
        headers,
        b"{not-json",
    )
    assert code == status
    assert constructed is False
    assert body_read is False
    assert_security_headers(response_headers)


def test_hostile_get_is_rejected_before_service(cockpit, monkeypatch):
    constructed = False

    def fail_constructor(*args, **kwargs):
        nonlocal constructed
        constructed = True
        raise AssertionError("WorkHunter must not be constructed")

    monkeypatch.setattr("work_hunter.web.server.WorkHunter", fail_constructor)
    code, headers, body = raw_request(
        cockpit,
        "GET",
        "/api/resumes",
        {"Host": f"evil.test:{cockpit.server_port}"},
        None,
    )
    assert code == 403
    assert json.loads(body)["error"] == "host_not_loopback"
    assert constructed is False
    assert_security_headers(headers)


@pytest.mark.parametrize(
    ("host_header", "origin_header", "listener_port"),
    [
        ("127.0.0.1:8787", "http://127.0.0.1:8787", 8787),
        ("localhost:8787", "http://localhost:8787", 8787),
        ("127.0.0.1", "http://127.0.0.1", 80),
        ("127.0.0.1:80", "http://127.0.0.1", 80),
    ],
)
def test_identical_http_origins_are_accepted(host_header, origin_header, listener_port):
    assert boundary_error(host_header, origin_header, listener_port=listener_port) is None


@pytest.mark.parametrize(
    ("host_header", "origin_header"),
    [
        ("127.0.0.1:8787", "http://localhost:8787"),
        ("localhost:8787", "http://127.0.0.1:8787"),
        ("127.0.0.1:8787", "http://127.0.0.2:8787"),
        ("[::1]:8787", "http://[::1]:8787"),
    ],
)
def test_distinct_or_unsupported_loopback_origins_are_rejected(host_header, origin_header):
    error = boundary_error(host_header, origin_header)
    assert error is not None
    assert error[0] == HTTPStatus.FORBIDDEN


@pytest.mark.parametrize(
    ("header_name", "value_template"),
    [
        ("Host", "127.0.0.1:nope"),
        ("Host", "[::1"),
        ("Host", "user@127.0.0.1:{port}"),
        ("Host", "127.0.0.1:{port}/path"),
        ("Host", "127.0.0.1:99999"),
        ("Origin", "http://127.0.0.1:nope"),
        ("Origin", "http://[::1"),
        ("Origin", "http://user@127.0.0.1:{port}"),
        ("Origin", "http://127.0.0.1:{port}/path"),
        ("Origin", "http://127.0.0.1:{port}?query=yes"),
        ("Origin", "http://127.0.0.1:{port}#fragment"),
    ],
)
def test_malformed_authority_returns_controlled_forbidden_before_body_or_service(
    cockpit, monkeypatch, header_name, value_template
):
    activity = {"body_read": False, "constructed": False}

    def fail_constructor(*args, **kwargs):
        activity["constructed"] = True
        raise AssertionError("WorkHunter must not be constructed")

    def fail_read_json(*args, **kwargs):
        activity["body_read"] = True
        raise AssertionError("request body must not be parsed")

    host = f"127.0.0.1:{cockpit.server_port}"
    headers = {"Host": host, "Content-Type": "application/json"}
    headers[header_name] = value_template.format(port=cockpit.server_port)
    monkeypatch.setattr("work_hunter.web.server.WorkHunter", fail_constructor)
    monkeypatch.setattr(cockpit.RequestHandlerClass, "_read_json", fail_read_json)
    code, response_headers, body = raw_request(
        cockpit,
        "POST",
        "/api/score",
        headers,
        b"{not-json",
    )
    assert code == 403
    assert json.loads(body)["error"] in {"host_not_loopback", "cross_origin_request"}
    assert activity == {"body_read": False, "constructed": False}
    assert_security_headers(response_headers)


@pytest.mark.parametrize("duplicate_header", ["Host", "Origin"])
def test_duplicate_authority_headers_are_rejected_before_dispatch(
    cockpit, monkeypatch, duplicate_header
):
    activity = {"body_read": False, "constructed": False}

    def fail_constructor(*args, **kwargs):
        activity["constructed"] = True
        raise AssertionError("WorkHunter must not be constructed")

    def fail_read_json(*args, **kwargs):
        activity["body_read"] = True
        raise AssertionError("request body must not be parsed")

    host = f"127.0.0.1:{cockpit.server_port}"
    headers = [("Host", host), ("Content-Type", "application/json")]
    if duplicate_header == "Host":
        headers.insert(1, ("Host", host))
    else:
        headers.extend([("Origin", f"http://{host}"), ("Origin", f"http://{host}")])
    monkeypatch.setattr("work_hunter.web.server.WorkHunter", fail_constructor)
    monkeypatch.setattr(cockpit.RequestHandlerClass, "_read_json", fail_read_json)
    code, response_headers, _ = raw_request_with_header_pairs(
        cockpit,
        "POST",
        "/api/score",
        headers,
        b"{not-json",
    )
    assert code == 403
    assert activity == {"body_read": False, "constructed": False}
    assert_security_headers(response_headers)


@pytest.mark.parametrize(
    ("parser_name", "value"),
    [
        ("_host_authority", "127.0.0.\t1:8787"),
        ("_host_authority", "127.0.0.\r1:8787"),
        ("_host_authority", "127.0.0.\n1:8787"),
        ("_host_authority", "127.0.0. 1:8787"),
        ("_host_authority", "127.0.0.\x001:8787"),
        ("_host_authority", "local\u00a0host:8787"),
        ("_host_authority", "127.0.0.1\uff1a8787"),
        ("_http_origin", "h\tttp://127.0.0.1:8787"),
        ("_http_origin", "ht\rtp://127.0.0.1:8787"),
        ("_http_origin", "htt\np://127.0.0.1:8787"),
        ("_http_origin", "http://127.0.0. 1:8787"),
        ("_http_origin", "http://127.0.0.\x001:8787"),
        ("_http_origin", "http://local\u00a0host:8787"),
        ("_http_origin", "http\uff1a//127.0.0.1:8787"),
    ],
)
def test_authority_rejects_non_visible_ascii_before_urlsplit(
    monkeypatch, parser_name, value
):
    parsed = False

    def fail_urlsplit(*args, **kwargs):
        nonlocal parsed
        parsed = True
        raise AssertionError("malformed authority must be rejected before urlsplit")

    monkeypatch.setattr(web_security, "urlsplit", fail_urlsplit)
    assert getattr(web_security, parser_name)(value) is None
    assert parsed is False


@pytest.mark.parametrize(
    ("header_name", "value_template"),
    [
        ("Host", "127.0.0.\t1:{port}"),
        ("Origin", "h\tttp://127.0.0.1:{port}"),
    ],
)
def test_embedded_tab_authority_is_forbidden_before_dispatch(
    cockpit, monkeypatch, header_name, value_template
):
    constructed = False

    def fail_constructor(*args, **kwargs):
        nonlocal constructed
        constructed = True
        raise AssertionError("WorkHunter must not be constructed")

    host = f"127.0.0.1:{cockpit.server_port}"
    headers = {"Host": host}
    headers[header_name] = value_template.format(port=cockpit.server_port)
    monkeypatch.setattr("work_hunter.web.server.WorkHunter", fail_constructor)
    code, response_headers, body = raw_request(
        cockpit,
        "GET",
        "/api/resumes",
        headers,
        None,
    )
    assert code == 403
    assert json.loads(body)["error"] in {"host_not_loopback", "cross_origin_request"}
    assert constructed is False
    assert_security_headers(response_headers)


def test_rejected_body_responses_close_gracefully_under_synchronized_stress(
    cockpit, monkeypatch
):
    boundary_entered = threading.Event()
    body_sent = threading.Event()
    original_boundary = web_server.request_boundary_error

    def synchronized_boundary(**kwargs):
        error = original_boundary(**kwargs)
        if error is not None:
            boundary_entered.set()
            if not body_sent.wait(timeout=5):
                raise AssertionError("client did not queue rejected request body")
        return error

    def fail_dispatch(*args, **kwargs):
        raise AssertionError("rejected request must not reach dispatch")

    monkeypatch.setattr(web_server, "request_boundary_error", synchronized_boundary)
    monkeypatch.setattr(web_server, "WorkHunter", fail_dispatch)
    monkeypatch.setattr(cockpit.RequestHandlerClass, "_read_json", fail_dispatch)
    request_body = b"x" * (64 * 1024)

    for attempt in range(16):
        boundary_entered.clear()
        body_sent.clear()
        with socket.create_connection(("127.0.0.1", cockpit.server_port), timeout=5) as connection:
            connection.settimeout(5)
            request_headers = (
                "POST /api/score HTTP/1.1\r\n"
                "Host: evil.test\r\n"
                "Content-Type: application/json\r\n"
                f"Content-Length: {len(request_body)}\r\n"
                "Connection: close\r\n"
                "\r\n"
            ).encode("ascii")
            connection.sendall(request_headers)
            assert boundary_entered.wait(timeout=5), f"boundary not reached on attempt {attempt}"
            connection.sendall(request_body)
            body_sent.set()
            response_chunks = []
            try:
                while chunk := connection.recv(64 * 1024):
                    response_chunks.append(chunk)
            except OSError as exc:
                pytest.fail(f"response connection aborted on attempt {attempt}: {exc!r}")

        response = b"".join(response_chunks)
        raw_headers, response_body = response.split(b"\r\n\r\n", 1)
        header_lines = raw_headers.decode("iso-8859-1").split("\r\n")
        response_headers = {
            name.lower(): value.strip()
            for name, value in (line.split(":", 1) for line in header_lines[1:])
        }
        assert header_lines[0].split()[1] == "403"
        assert len(response_body) == int(response_headers["content-length"])
        assert json.loads(response_body)["error"] == "host_not_loopback"
        assert response_headers["connection"] == "close"
        assert_security_headers(response_headers)


def test_rejected_body_discard_has_a_short_deadline(cockpit, monkeypatch):
    discard_finished = threading.Event()
    original_discard = cockpit.RequestHandlerClass._discard_rejected_body

    def tracked_discard(handler):
        try:
            original_discard(handler)
        finally:
            discard_finished.set()

    def fail_dispatch(*args, **kwargs):
        raise AssertionError("rejected request must not reach dispatch")

    monkeypatch.setattr(web_server, "WorkHunter", fail_dispatch)
    monkeypatch.setattr(cockpit.RequestHandlerClass, "_read_json", fail_dispatch)
    monkeypatch.setattr(cockpit.RequestHandlerClass, "_discard_rejected_body", tracked_discard)
    with socket.create_connection(("127.0.0.1", cockpit.server_port), timeout=5) as connection:
        connection.settimeout(5)
        request_headers = (
            "POST /api/score HTTP/1.1\r\n"
            "Host: evil.test\r\n"
            "Content-Type: application/json\r\n"
            "Content-Length: 1000000000\r\n"
            "Connection: close\r\n"
            "\r\n"
        ).encode("ascii")
        started = time.monotonic()
        connection.sendall(request_headers)
        response_chunks = []
        while chunk := connection.recv(64 * 1024):
            response_chunks.append(chunk)
        assert discard_finished.wait(timeout=2)
        elapsed = time.monotonic() - started

    response = b"".join(response_chunks)
    raw_headers, response_body = response.split(b"\r\n\r\n", 1)
    response_headers = {
        name.lower(): value.strip()
        for name, value in (
            line.split(":", 1)
            for line in raw_headers.decode("iso-8859-1").split("\r\n")[1:]
        )
    }
    assert elapsed < 2
    assert raw_headers.startswith(b"HTTP/1.0 403")
    assert json.loads(response_body)["error"] == "host_not_loopback"
    assert_security_headers(response_headers)


def test_rejected_body_discard_handles_unreasonable_content_length(cockpit, monkeypatch):
    handler_error = threading.Event()
    discard_finished = threading.Event()
    original_discard = cockpit.RequestHandlerClass._discard_rejected_body

    def tracked_discard(handler):
        try:
            original_discard(handler)
        finally:
            discard_finished.set()

    def record_handler_error(*args, **kwargs):
        handler_error.set()

    def fail_dispatch(*args, **kwargs):
        raise AssertionError("rejected request must not reach dispatch")

    monkeypatch.setattr(web_server, "WorkHunter", fail_dispatch)
    monkeypatch.setattr(cockpit.RequestHandlerClass, "_read_json", fail_dispatch)
    monkeypatch.setattr(cockpit.RequestHandlerClass, "_discard_rejected_body", tracked_discard)
    monkeypatch.setattr(cockpit, "handle_error", record_handler_error)
    with socket.create_connection(("127.0.0.1", cockpit.server_port), timeout=5) as connection:
        connection.settimeout(5)
        request_headers = (
            "POST /api/score HTTP/1.1\r\n"
            "Host: evil.test\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {'9' * 5000}\r\n"
            "Connection: close\r\n"
            "\r\n"
        ).encode("ascii")
        connection.sendall(request_headers)
        response_chunks = []
        while chunk := connection.recv(64 * 1024):
            response_chunks.append(chunk)

    assert discard_finished.wait(timeout=2)
    assert handler_error.wait(timeout=0.5) is False
    response = b"".join(response_chunks)
    raw_headers, response_body = response.split(b"\r\n\r\n", 1)
    assert raw_headers.startswith(b"HTTP/1.0 403")
    assert json.loads(response_body)["error"] == "host_not_loopback"


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
    assert_security_headers(headers)


@pytest.mark.parametrize(
    ("method", "path", "headers", "body", "status"),
    [
        ("GET", "/missing", {}, None, 404),
        ("POST", "/api/score", {"Host": "evil.test", "Content-Type": "application/json"}, b"{}", 403),
        ("POST", "/api/score", {"Content-Type": "text/plain"}, b"{}", 415),
        ("OPTIONS", "/api/score", {}, None, 405),
    ],
)
def test_explicit_error_responses_include_security_headers(
    cockpit, method, path, headers, body, status
):
    code, response_headers, _ = raw_request(cockpit, method, path, headers, body)
    assert code == status
    assert_security_headers(response_headers)


def test_options_is_rejected_without_cors_authorization_headers(cockpit):
    code, headers, _ = raw_request(cockpit, "OPTIONS", "/api/score", {}, None)
    assert code == 405
    assert "access-control-allow-origin" not in headers
    assert "access-control-allow-methods" not in headers
    assert "access-control-allow-headers" not in headers
    assert headers["allow"] == "GET, POST"


def test_masked_config_http_round_trip_never_persists_mask(cockpit, tmp_path):
    app = WorkHunter(tmp_path)
    app.config["ai"]["api_key"] = "saved-ai-value"
    app.config["sources"]["hh"]["access_token"] = "saved-hh-value"
    app.save_config(app.config)
    status, _, raw = raw_request(cockpit, "GET", "/api/config", {}, None)
    masked = json.loads(raw)
    assert status == 200
    assert masked["ai"]["api_key"] == "***"
    assert masked["sources"]["hh"]["access_token"] == "***"

    host = f"127.0.0.1:{cockpit.server_port}"
    status, _, _ = raw_request(
        cockpit,
        "POST",
        "/api/config",
        {"Host": host, "Content-Type": "application/json"},
        json.dumps(masked).encode("utf-8"),
    )

    assert status == 200
    reloaded = WorkHunter(tmp_path)
    assert reloaded.config["ai"]["api_key"] == "saved-ai-value"
    assert reloaded.config["sources"]["hh"]["access_token"] == "saved-hh-value"


@pytest.mark.parametrize(
    ("has_stored_value", "stored_value", "submitted_value", "expected_value"),
    [
        (
            False,
            None,
            {"access_token": "***", "label": "new-dict"},
            {"label": "new-dict"},
        ),
        (
            False,
            None,
            [{"refresh_token": "", "label": "new-list"}],
            [{"label": "new-list"}],
        ),
        (
            True,
            "legacy-scalar",
            {"client_secret": None, "label": "scalar-to-dict"},
            {"label": "scalar-to-dict"},
        ),
        (
            True,
            None,
            [{"api_key": "***", "label": "null-to-list"}],
            [{"label": "null-to-list"}],
        ),
    ],
)
def test_config_service_sanitizes_nested_placeholders_before_save(
    tmp_path,
    has_stored_value,
    stored_value,
    submitted_value,
    expected_value,
):
    app = WorkHunter(tmp_path)
    if has_stored_value:
        app.config["new_plugin"] = stored_value
    app.save_config(app.config)

    response = app.update_config_from_client({"new_plugin": submitted_value})
    reloaded = WorkHunter(tmp_path)

    assert response["new_plugin"] == expected_value
    assert reloaded.config["new_plugin"] == expected_value


def test_config_http_update_cannot_clear_secret_via_parent_replacement(
    cockpit,
    tmp_path,
):
    app = WorkHunter(tmp_path)
    app.config["ai"]["api_key"] = "saved-ai-value"
    app.save_config(app.config)
    host = f"127.0.0.1:{cockpit.server_port}"

    status, _, _ = raw_request(
        cockpit,
        "POST",
        "/api/config",
        {"Host": host, "Content-Type": "application/json"},
        json.dumps({"ai": None}).encode("utf-8"),
    )

    assert status == 200
    reloaded = WorkHunter(tmp_path)
    assert reloaded.config["ai"]["api_key"] == "saved-ai-value"


@pytest.mark.parametrize(
    ("submitted", "expected"),
    [
        pytest.param(
            [{"id": "two", "refresh_token": "***", "label": "new-two"}],
            [{"id": "two", "refresh_token": "saved-two", "label": "new-two"}],
            id="remove-first",
        ),
        pytest.param(
            [
                {"id": "zero", "access_token": "***", "label": "new-zero"},
                {"id": "one", "access_token": "***", "label": "new-one"},
                {"id": "two", "refresh_token": "***", "label": "new-two"},
            ],
            [
                {"id": "zero", "label": "new-zero"},
                {"id": "one", "access_token": "saved-one", "label": "new-one"},
                {"id": "two", "refresh_token": "saved-two", "label": "new-two"},
            ],
            id="insert-first",
        ),
        pytest.param(
            [
                {"id": "two", "refresh_token": "***", "label": "new-two"},
                {"id": "one", "access_token": "***", "label": "new-one"},
            ],
            [
                {"id": "two", "refresh_token": "saved-two", "label": "new-two"},
                {"id": "one", "access_token": "saved-one", "label": "new-one"},
            ],
            id="reorder",
        ),
        pytest.param(
            [
                {"id": "one", "access_token": "***", "label": "new-one"},
                {"id": "two", "refresh_token": "***", "label": "new-two"},
                {
                    "id": "three",
                    "client_secret": "new-three-secret",
                    "label": "new-three",
                },
            ],
            [
                {"id": "one", "access_token": "saved-one", "label": "new-one"},
                {"id": "two", "refresh_token": "saved-two", "label": "new-two"},
                {
                    "id": "three",
                    "client_secret": "new-three-secret",
                    "label": "new-three",
                },
            ],
            id="append",
        ),
    ],
)
def test_config_service_merges_secret_lists_by_stable_identity(
    tmp_path,
    submitted,
    expected,
):
    app = WorkHunter(tmp_path)
    app.config["custom"] = [
        {"id": "one", "access_token": "saved-one", "label": "old-one"},
        {"id": "two", "refresh_token": "saved-two", "label": "old-two"},
    ]
    app.save_config(app.config)

    app.update_config_from_client({"custom": submitted})
    reloaded = WorkHunter(tmp_path)

    assert reloaded.config["custom"] == expected


@pytest.mark.parametrize(
    ("stored", "submitted", "expected"),
    [
        pytest.param(
            [
                {"id": "duplicate", "access_token": "saved-left", "label": "left"},
                {"id": "duplicate", "access_token": "saved-right", "label": "right"},
            ],
            [
                {"id": "duplicate", "access_token": "***", "label": "right"},
                {"id": "duplicate", "access_token": "***", "label": "left"},
            ],
            [
                {"id": "duplicate", "label": "right"},
                {"id": "duplicate", "label": "left"},
            ],
            id="ambiguous-identity",
        ),
        pytest.param(
            [
                {"access_token": "saved-left", "label": "left"},
                {"refresh_token": "saved-right", "label": "right"},
            ],
            [{"refresh_token": "***", "label": "right"}],
            [{"label": "right"}],
            id="no-identity",
        ),
        pytest.param(
            [
                {"name": "left", "access_token": "saved-left"},
                {"name": "right", "access_token": "saved-right"},
            ],
            [{"name": "right", "access_token": "***", "label": "renamed"}],
            [{"name": "right", "label": "renamed"}],
            id="mutable-name-is-not-identity",
        ),
        pytest.param(
            [
                {"slug": "left", "access_token": "saved-left"},
                {"slug": "right", "access_token": "saved-right"},
            ],
            [{"slug": "right", "access_token": "***", "label": "renamed"}],
            [{"slug": "right", "label": "renamed"}],
            id="mutable-slug-is-not-identity",
        ),
    ],
)
def test_config_service_scrubs_unmatched_secret_list_placeholders(
    tmp_path,
    stored,
    submitted,
    expected,
):
    app = WorkHunter(tmp_path)
    app.config["custom"] = stored
    app.save_config(app.config)

    app.update_config_from_client({"custom": submitted})
    reloaded = WorkHunter(tmp_path)

    assert reloaded.config["custom"] == expected


def test_masked_config_http_round_trip_preserves_secrets_inside_lists(
    cockpit,
    tmp_path,
):
    app = WorkHunter(tmp_path)
    app.config["custom"] = [
        {
            "id": "zero",
            "access_token": "saved-list-zero",
            "enabled": False,
            "label": "old-zero",
        },
        {
            "id": "one",
            "refresh_token": "saved-list-one",
            "label": "old-one",
        },
    ]
    app.save_config(app.config)
    status, _, raw = raw_request(cockpit, "GET", "/api/config", {}, None)
    masked = json.loads(raw)
    assert status == 200
    assert masked["custom"][0]["access_token"] == "***"
    assert masked["custom"][1]["refresh_token"] == "***"
    masked["custom"][0].update({"enabled": True, "label": "new-zero"})
    masked["custom"][1]["label"] = "new-one"
    masked["custom"].extend(
        [
            {
                "id": "two",
                "client_secret": "***",
                "label": "new-placeholder",
            },
            {
                "id": "three",
                "client_secret": "new-list-secret",
                "label": "new-configured",
            },
        ]
    )
    host = f"127.0.0.1:{cockpit.server_port}"

    status, _, response_raw = raw_request(
        cockpit,
        "POST",
        "/api/config",
        {"Host": host, "Content-Type": "application/json"},
        json.dumps(masked).encode("utf-8"),
    )

    assert status == 200
    response = json.loads(response_raw)
    assert response["custom"][0] == {
        "id": "zero",
        "access_token": "***",
        "enabled": True,
        "label": "new-zero",
    }
    assert response["custom"][1] == {
        "id": "one",
        "refresh_token": "***",
        "label": "new-one",
    }
    assert response["custom"][2] == {
        "id": "two",
        "label": "new-placeholder",
    }
    assert response["custom"][3] == {
        "id": "three",
        "client_secret": "***",
        "label": "new-configured",
    }
    reloaded = WorkHunter(tmp_path)
    assert reloaded.config["custom"] == [
        {
            "id": "zero",
            "access_token": "saved-list-zero",
            "enabled": True,
            "label": "new-zero",
        },
        {
            "id": "one",
            "refresh_token": "saved-list-one",
            "label": "new-one",
        },
        {"id": "two", "label": "new-placeholder"},
        {
            "id": "three",
            "client_secret": "new-list-secret",
            "label": "new-configured",
        },
    ]


@pytest.mark.parametrize("confirm", [None, False, "true", 1])
def test_clear_config_secret_requires_literal_true(tmp_path, confirm):
    app = WorkHunter(tmp_path)
    app.config["sources"]["hh"]["access_token"] = "saved-access-value"

    result = app.clear_config_secret(
        "sources.hh.access_token",
        confirm=confirm,
    )

    assert result["status"] == "blocked"
    assert app.config["sources"]["hh"]["access_token"] == "saved-access-value"


def test_clear_config_secret_rejects_unknown_path_and_clears_only_selected(tmp_path):
    app = WorkHunter(tmp_path)
    app.config["sources"]["hh"].update(
        {
            "access_token": "saved-access-value",
            "refresh_token": "saved-refresh-value",
        }
    )

    rejected = app.clear_config_secret("sources.hh.enabled", confirm=True)
    cleared = app.clear_config_secret("sources.hh.access_token", confirm=True)

    assert rejected["status"] == "blocked"
    assert cleared == {"status": "ok", "path": "sources.hh.access_token"}
    assert app.config["sources"]["hh"]["access_token"] == ""
    assert app.config["sources"]["hh"]["refresh_token"] == "saved-refresh-value"


def test_clear_config_secret_http_route_clears_only_with_literal_confirmation(
    cockpit,
    tmp_path,
):
    app = WorkHunter(tmp_path)
    app.config["sources"]["hh"].update(
        {
            "access_token": "saved-access-value",
            "refresh_token": "saved-refresh-value",
        }
    )
    app.save_config(app.config)
    host = f"127.0.0.1:{cockpit.server_port}"
    headers = {"Host": host, "Content-Type": "application/json"}

    blocked_status, _, blocked_raw = raw_request(
        cockpit,
        "POST",
        "/api/config/secret/clear",
        headers,
        json.dumps(
            {"path": "sources.hh.access_token", "confirm": "true"}
        ).encode("utf-8"),
    )
    clear_status, _, clear_raw = raw_request(
        cockpit,
        "POST",
        "/api/config/secret/clear",
        headers,
        json.dumps(
            {"path": "sources.hh.access_token", "confirm": True}
        ).encode("utf-8"),
    )

    assert blocked_status == 200
    assert json.loads(blocked_raw)["status"] == "blocked"
    assert clear_status == 200
    assert json.loads(clear_raw) == {
        "status": "ok",
        "path": "sources.hh.access_token",
    }
    reloaded = WorkHunter(tmp_path)
    assert reloaded.config["sources"]["hh"]["access_token"] == ""
    assert reloaded.config["sources"]["hh"]["refresh_token"] == "saved-refresh-value"
