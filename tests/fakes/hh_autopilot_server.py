from __future__ import annotations

import json
import socket
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import RLock, Thread
from typing import Self, cast
from urllib.parse import parse_qs, urlsplit


@dataclass(frozen=True)
class FakeHHRequest:
    method: str
    path: str
    vacancy_id: str
    resume_id: str
    timestamp: str


@dataclass
class FakeHHState:
    application_scenarios: dict[str, str] = field(default_factory=dict)
    negotiations: list[dict[str, object]] = field(default_factory=list)
    valid_token: str = "private-access-token"
    _requests: list[FakeHHRequest] = field(default_factory=list, repr=False)
    _lock: RLock = field(default_factory=RLock, repr=False)

    @property
    def requests(self) -> tuple[FakeHHRequest, ...]:
        with self._lock:
            return tuple(self._requests)

    def record(
        self,
        method: str,
        path: str,
        *,
        vacancy_id: str = "",
        resume_id: str = "",
    ) -> None:
        entry = FakeHHRequest(
            method=method,
            path=path,
            vacancy_id=vacancy_id,
            resume_id=resume_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        with self._lock:
            self._requests.append(entry)

    def add_negotiation(
        self,
        *,
        vacancy_id: str,
        resume_id: str,
    ) -> dict[str, object]:
        with self._lock:
            negotiation: dict[str, object] = {
                "id": f"n-{len(self.negotiations) + 1}",
                "vacancy": {"id": vacancy_id},
                "resume": {"id": resume_id},
                "created_at": datetime.now(timezone.utc).isoformat(),
                "status": "active",
            }
            self.negotiations.append(negotiation)
            return dict(negotiation)

    def negotiation_page(
        self,
        *,
        status: str,
        page: int,
        per_page: int,
    ) -> dict[str, object]:
        with self._lock:
            rows = [
                dict(row)
                for row in self.negotiations
                if str(row.get("status") or "") == status
            ]
        total = len(rows)
        pages = (total + per_page - 1) // per_page if total else 0
        start = page * per_page
        return {
            "items": rows[start : start + per_page],
            "page": page,
            "pages": pages,
            "per_page": per_page,
            "found": total,
        }


class _FakeHHHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, state: FakeHHState) -> None:
        self.state = state
        super().__init__(("127.0.0.1", 0), _FakeHHHandler)


class _FakeHHHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    @property
    def fake_server(self) -> _FakeHHHTTPServer:
        return cast(_FakeHHHTTPServer, self.server)

    def do_GET(self) -> None:
        target = urlsplit(self.path)
        self.fake_server.state.record("GET", target.path)
        if not self._authorized():
            self._reply_json(401, {"errors": [{"value": "auth_expired"}]})
            return
        if target.path == "/negotiations":
            query = parse_qs(target.query)
            status = query.get("status", ["active"])[0]
            page = int(query.get("page", ["0"])[0])
            per_page = int(query.get("per_page", ["100"])[0])
            self._reply_json(
                200,
                self.fake_server.state.negotiation_page(
                    status=status,
                    page=page,
                    per_page=per_page,
                ),
            )
            return
        self._reply_json(404, {"errors": [{"value": "not_found"}]})

    def do_POST(self) -> None:
        target = urlsplit(self.path)
        if target.path != "/negotiations":
            self._reply_json(404, {"errors": [{"value": "not_found"}]})
            return
        payload = self._read_form()
        vacancy_id = payload.get("vacancy_id", "")
        resume_id = payload.get("resume_id", "")
        self.fake_server.state.record(
            "POST",
            target.path,
            vacancy_id=vacancy_id,
            resume_id=resume_id,
        )
        if not self._authorized():
            self._reply_json(401, {"errors": [{"value": "auth_expired"}]})
            return

        negotiation = self.fake_server.state.add_negotiation(
            vacancy_id=vacancy_id,
            resume_id=resume_id,
        )
        scenario = self.fake_server.state.application_scenarios.get(
            vacancy_id,
            "created",
        )
        if scenario == "accepted_then_close":
            self.close_connection = True
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.connection.close()
            return
        self._reply_json(
            201,
            {"id": negotiation["id"]},
            headers={"Location": f"/negotiations/{negotiation['id']}"},
        )

    def _authorized(self) -> bool:
        return (
            self.headers.get("Authorization")
            == f"Bearer {self.fake_server.state.valid_token}"
        )

    def _read_form(self) -> dict[str, str]:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8")
        return {
            key: values[0]
            for key, values in parse_qs(body, keep_blank_values=True).items()
            if values
        }

    def _reply_json(
        self,
        status: int,
        payload: dict[str, object],
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


class FakeHHServer:
    def __init__(self) -> None:
        self.state = FakeHHState()
        self._server = _FakeHHHTTPServer(self.state)
        self._thread = Thread(
            target=self._server.serve_forever,
            name="fake-hh-contract-server",
            daemon=True,
        )
        self._started = False

    @property
    def base_url(self) -> str:
        host, port = cast(tuple[str, int], self._server.server_address)
        return f"http://{host}:{port}"

    @property
    def requests(self) -> tuple[FakeHHRequest, ...]:
        return self.state.requests

    @property
    def application_post_count(self) -> int:
        return sum(
            request.method == "POST" and request.path == "/negotiations"
            for request in self.requests
        )

    def scenario(self, vacancy_id: str, outcome: str) -> None:
        if outcome not in {"created", "accepted_then_close"}:
            raise ValueError("unsupported fake HH outcome")
        self.state.application_scenarios[vacancy_id] = outcome

    def start(self) -> None:
        if not self._started:
            self._thread.start()
            self._started = True

    def close(self) -> None:
        if self._started:
            self._server.shutdown()
            self._thread.join(timeout=5)
        self._server.server_close()

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()


__all__ = ["FakeHHRequest", "FakeHHServer", "FakeHHState"]
