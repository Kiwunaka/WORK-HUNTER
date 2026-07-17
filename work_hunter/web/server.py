from __future__ import annotations

import json
import mimetypes
import socket
import time
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qs, urlparse

from ..config import mask_secrets
from ..models import CalendarEvent, Resume, SavedSearch
from ..safety import is_literal_confirmation
from ..services import WorkHunter
from .security import SECURITY_HEADERS, ensure_loopback_listener, request_boundary_error


STATIC_DIR = Path(__file__).parent / "static"
REJECTED_BODY_DRAIN_LIMIT = 1024 * 1024
REJECTED_BODY_DRAIN_SECONDS = 0.25
UI_ROUTES = {
    "/today",
    "/jobs",
    "/applications",
    "/calendar",
    "/favorites",
    "/chat",
    "/assistant",
    "/agent",
    "/analytics",
    "/settings",
    "/sources",
    "/stats",
    "/trends",
}
APPLICATION_FUNNEL_STAGES = (
    "applied",
    "response",
    "phone_screen",
    "interview",
    "offer",
    "rejected",
)


@dataclass(frozen=True)
class _AutopilotMutationRoute:
    action: str
    account_required: bool = False
    allows_all: bool = False
    allows_global: bool = False
    confirmation_required: bool = False


HH_AUTOPILOT_GET_ROUTES = {
    "/api/hh/autopilot/config": "config",
    "/api/hh/autopilot/validate": "validate",
    "/api/hh/autopilot/status": "status",
    "/api/hh/autopilot/history": "history",
    "/api/hh/autopilot/challenges": "challenges",
}

HH_AUTOPILOT_MUTATION_ROUTES = {
    "/api/hh/autopilot/config": _AutopilotMutationRoute("config"),
    "/api/hh/autopilot/enable": _AutopilotMutationRoute(
        "enable", account_required=True, allows_all=True, confirmation_required=True
    ),
    "/api/hh/autopilot/disable": _AutopilotMutationRoute(
        "disable", account_required=True, allows_all=True, confirmation_required=True
    ),
    "/api/hh/autopilot/pause": _AutopilotMutationRoute(
        "pause", account_required=True, allows_all=True
    ),
    "/api/hh/autopilot/resume": _AutopilotMutationRoute(
        "resume", account_required=True, allows_all=True
    ),
    "/api/hh/autopilot/stop": _AutopilotMutationRoute("stop", account_required=True),
    "/api/hh/autopilot/kill-switch": _AutopilotMutationRoute(
        "kill-switch",
        account_required=True,
        allows_all=True,
        allows_global=True,
        confirmation_required=True,
    ),
    "/api/hh/autopilot/clear-kill-switch": _AutopilotMutationRoute(
        "clear-kill-switch",
        account_required=True,
        allows_all=True,
        allows_global=True,
        confirmation_required=True,
    ),
    "/api/hh/autopilot/shadow": _AutopilotMutationRoute(
        "shadow", account_required=True
    ),
    "/api/hh/autopilot/canary": _AutopilotMutationRoute(
        "canary", account_required=True, confirmation_required=True
    ),
    "/api/hh/autopilot/run-now": _AutopilotMutationRoute(
        "run-now", account_required=True, allows_all=True
    ),
    "/api/hh/autopilot/recover-now": _AutopilotMutationRoute(
        "recover-now", allows_all=True
    ),
    "/api/hh/autopilot/retry": _AutopilotMutationRoute("retry", account_required=True),
    "/api/hh/autopilot/resolve-challenge": _AutopilotMutationRoute(
        "resolve-challenge", account_required=True
    ),
}


def run_server(root: str | Path | None = None, host: str = "127.0.0.1", port: int = 8787) -> None:
    ensure_loopback_listener(host)
    bind_host = "127.0.0.1" if host.casefold() == "localhost" else host
    handler = make_handler(Path(root) if root is not None else Path.cwd())
    server = ThreadingHTTPServer((bind_host, port), handler)
    print(f"Work Hunter UI: http://{bind_host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def make_handler(root: Path):
    class WorkHunterHandler(BaseHTTPRequestHandler):
        server_version = "WorkHunterHTTP/0.1"

        def parse_request(self) -> bool:
            if not super().parse_request():
                return False
            host_values = self.headers.get_all("Host", [])
            origin_values = self.headers.get_all("Origin", [])
            boundary_error: tuple[HTTPStatus, str, str] | None
            if len(host_values) != 1:
                boundary_error = (
                    HTTPStatus.FORBIDDEN,
                    "host_not_loopback",
                    "Loopback Host header required.",
                )
            elif len(origin_values) > 1:
                boundary_error = (
                    HTTPStatus.FORBIDDEN,
                    "cross_origin_request",
                    "Cross-origin requests are blocked.",
                )
            else:
                server = cast(ThreadingHTTPServer, self.server)
                boundary_error = request_boundary_error(
                    method=self.command,
                    host_header=host_values[0],
                    origin_header=origin_values[0] if origin_values else None,
                    content_type=self.headers.get("Content-Type"),
                    listener_host=str(server.server_address[0]),
                    listener_port=server.server_port,
                )
            if boundary_error is None:
                return True
            status, error, message = boundary_error
            self._send_boundary_error(status, error, message)
            return False

        def _send_boundary_error(self, status: HTTPStatus, error: str, message: str) -> None:
            payload = json.dumps(
                {"error": error, "message": message},
                ensure_ascii=False,
            ).encode("utf-8")
            self.close_connection = True
            self.send_response(status)
            self.send_header("Connection", "close")
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            self.wfile.flush()
            try:
                self.connection.shutdown(socket.SHUT_WR)
            except OSError:
                pass
            self._discard_rejected_body()

        def _discard_rejected_body(self) -> None:
            content_lengths = self.headers.get_all("Content-Length", [])
            transfer_encodings = self.headers.get_all("Transfer-Encoding", [])
            target = 0
            if transfer_encodings or len(content_lengths) > 1:
                target = REJECTED_BODY_DRAIN_LIMIT
            elif content_lengths:
                raw_length = content_lengths[0]
                if (
                    raw_length.isascii()
                    and raw_length.isdigit()
                    and len(raw_length) <= len(str(REJECTED_BODY_DRAIN_LIMIT))
                ):
                    target = min(int(raw_length), REJECTED_BODY_DRAIN_LIMIT)
                else:
                    target = REJECTED_BODY_DRAIN_LIMIT
            if target <= 0:
                return

            deadline = time.monotonic() + REJECTED_BODY_DRAIN_SECONDS
            previous_timeout = self.connection.gettimeout()
            try:
                remaining = target
                while remaining > 0:
                    timeout = deadline - time.monotonic()
                    if timeout <= 0:
                        break
                    self.connection.settimeout(timeout)
                    try:
                        chunk = self.rfile.read1(min(remaining, 64 * 1024))
                    except (OSError, TimeoutError):
                        break
                    if not chunk:
                        break
                    remaining -= len(chunk)
            finally:
                try:
                    self.connection.settimeout(previous_timeout)
                except OSError:
                    pass

        def end_headers(self) -> None:
            for name, value in SECURITY_HEADERS.items():
                self.send_header(name, value)
            super().end_headers()

        def do_OPTIONS(self) -> None:
            payload = json.dumps({"error": "method_not_allowed"}).encode("utf-8")
            self.send_response(HTTPStatus.METHOD_NOT_ALLOWED)
            self.send_header("Allow", "GET, POST")
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            if path == "/" or path in UI_ROUTES:
                self._send_static("index.html")
                return
            autopilot_action = HH_AUTOPILOT_GET_ROUTES.get(path)
            if autopilot_action is not None:
                try:
                    query = parse_qs(parsed.query, keep_blank_values=True)
                    app = WorkHunter(root)
                    result = _dispatch_hh_autopilot_get(
                        app,
                        autopilot_action,
                        query,
                    )
                except Exception as exc:
                    self._send_json(
                        {"status": "blocked", "error": str(exc)},
                        HTTPStatus.BAD_REQUEST,
                    )
                    return
                self._send_json(mask_secrets(result))
                return
            if path == "/api/jobs":
                query = parse_qs(parsed.query)
                app = WorkHunter(root)
                jobs = app.list_jobs(
                    limit=_int_arg(query, "limit", 100),
                    source=_str_arg(query, "source"),
                    status=_str_arg(query, "status"),
                    min_score=_optional_int_arg(query, "min_score"),
                )
                self._send_json([_job_json(job, app.storage) for job in jobs])
                return
            if path == "/api/jobs/export":
                query = parse_qs(parsed.query)
                app = WorkHunter(root)
                csv_data = app.export_jobs(
                    status=_str_arg(query, "status"),
                    source=_str_arg(query, "source"),
                    format="csv",
                )
                csv_payload = csv_data.encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/csv; charset=utf-8")
                self.send_header("Content-Length", str(len(csv_payload)))
                self.end_headers()
                self.wfile.write(csv_payload)
                return
            if path.startswith("/api/jobs/"):
                if path.endswith("/note"):
                    job_id = _path_int(path.removesuffix("/note"), "/api/jobs/")
                    app = WorkHunter(root)
                    self._send_json({"body": app.storage.get_note(job_id)})
                    return
                job_id = _path_int(path, "/api/jobs/")
                app = WorkHunter(root)
                job = app.get_job(job_id)
                if job is None:
                    self._send_json({"error": "not_found"}, HTTPStatus.NOT_FOUND)
                    return
                job_payload = job.to_dict()
                letter = app.latest_letter(job_id)
                job_payload["latest_letter"] = letter.to_dict() if letter else None
                self._send_json(job_payload)
                return
            if path == "/api/resumes":
                app = WorkHunter(root)
                query = parse_qs(parsed.query)
                profile_id = _str_arg(query, "profile_id") or "default"
                profiles = app.config.get("profiles") or {}
                if profile_id not in profiles:
                    self._send_json(
                        {"error": "invalid_profile"}, HTTPStatus.BAD_REQUEST
                    )
                    return
                resumes = app.storage.list_resumes(profile_id=profile_id)
                self._send_json([r.to_dict() for r in resumes])
                return
            if path == "/api/hh/resumes":
                app = WorkHunter(root)
                self._send_json([r.to_dict() for r in app.storage.list_hh_resumes()])
                return
            if path == "/api/hh/whoami":
                app = WorkHunter(root)
                self._send_json(app.hh_whoami())
                return
            if path == "/api/hh/auth/status":
                app = WorkHunter(root)
                self._send_json(app.hh_auth_status())
                return
            if path == "/api/hh/web/status":
                app = WorkHunter(root)
                self._send_json(app.hh_web_status())
                return
            if path == "/api/hh/negotiations":
                app = WorkHunter(root)
                self._send_json([n.to_dict() for n in app.storage.list_hh_negotiations()])
                return
            if path == "/api/hh/employers":
                app = WorkHunter(root)
                self._send_json([e.to_dict() for e in app.storage.list_hh_employers()])
                return
            if path == "/api/hh/contacts":
                app = WorkHunter(root)
                self._send_json([c.to_dict() for c in app.storage.list_hh_contacts()])
                return
            if path == "/api/hh/skipped":
                app = WorkHunter(root)
                self._send_json([s.to_dict() for s in app.storage.list_hh_skipped_vacancies()])
                return
            if path == "/api/hh/summary":
                app = WorkHunter(root)
                self._send_json(app.hh_operator_summary())
                return
            if path == "/api/hh/lab/quick-calls":
                app = WorkHunter(root)
                self._send_json(app.list_hh_api_lab_quick_calls())
                return
            if path == "/api/hh/lab/snippets":
                app = WorkHunter(root)
                self._send_json(app.list_hh_api_lab_snippets())
                return
            if path == "/api/agent/preflight":
                query = parse_qs(parsed.query)
                app = WorkHunter(root)
                self._send_json(app.hh_agent_preflight(live_auth=_bool_arg(query, "live_auth", False)))
                return
            if path == "/api/agent/digest":
                query = parse_qs(parsed.query)
                app = WorkHunter(root)
                self._send_json(app.hh_agent_digest(limit=_int_arg(query, "limit", 10)))
                return
            if path == "/api/agent/events":
                query = parse_qs(parsed.query)
                app = WorkHunter(root)
                self._send_json(
                    app.list_hh_agent_events(
                        status=_str_arg(query, "status"),
                        limit=_optional_int_arg(query, "limit"),
                    )
                )
                return
            if path == "/api/agent/tasks":
                query = parse_qs(parsed.query)
                app = WorkHunter(root)
                self._send_json(
                    app.list_hh_agent_tasks(
                        status=_str_arg(query, "status"),
                        limit=_optional_int_arg(query, "limit"),
                    )
                )
                return
            if path == "/api/agent/agenda.md":
                app = WorkHunter(root)
                self._send_text(app.export_hh_agent_agenda_markdown(), content_type="text/markdown; charset=utf-8")
                return
            if path == "/api/agent/calendar.ics":
                app = WorkHunter(root)
                self._send_text(app.export_hh_agent_calendar_ics(), content_type="text/calendar; charset=utf-8")
                return
            if path in {"/api/agent/operations", "/api/operations"}:
                query = parse_qs(parsed.query)
                app = WorkHunter(root)
                self._send_json(app.list_hh_agent_operations(limit=_int_arg(query, "limit", 50)))
                return
            if path.startswith("/api/operation-status/"):
                operation_id = _path_int(path, "/api/operation-status/")
                app = WorkHunter(root)
                self._send_json(app.hh_agent_operation_status(operation_id))
                return
            if path.startswith("/api/agent/operations/"):
                operation_id = _path_int(path, "/api/agent/operations/")
                app = WorkHunter(root)
                self._send_json(app.hh_agent_operation_status(operation_id))
                return
            if path in {"/api/agent/approvals", "/api/approvals"}:
                query = parse_qs(parsed.query)
                app = WorkHunter(root)
                self._send_json(app.list_hh_approvals(status=_str_arg(query, "status")))
                return
            if path in {"/api/agent/templates", "/api/templates"}:
                app = WorkHunter(root)
                self._send_json(app.list_hh_letter_templates())
                return
            if path in {"/api/agent/blacklist", "/api/blacklist"}:
                app = WorkHunter(root)
                self._send_json(app.list_hh_employer_blacklist())
                return
            if path == "/api/hh/presets":
                app = WorkHunter(root)
                self._send_json(app.list_hh_campaign_presets())
                return
            if path == "/api/events":
                query = parse_qs(parsed.query)
                app = WorkHunter(root)
                events = app.storage.list_events(
                    from_date=_str_arg(query, "from") or "",
                    to_date=_str_arg(query, "to") or "",
                )
                self._send_json([e.to_dict() for e in events])
                return
            if path == "/api/saved-searches":
                app = WorkHunter(root)
                searches = app.storage.list_searches()
                self._send_json([s.to_dict() for s in searches])
                return
            if path == "/api/ghost-jobs":
                query = parse_qs(parsed.query)
                app = WorkHunter(root)
                days = _int_arg(query, "days", 7)
                jobs = app.storage.get_ghost_jobs(
                    days=days,
                    profile_id=app.active_profile_id(),
                )
                self._send_json([j.to_dict() for j in jobs])
                return
            if path == "/api/behavior-stats":
                app = WorkHunter(root)
                stats = app.storage.get_behavior_stats()
                self._send_json(stats)
                return
            if path == "/api/report":
                app = WorkHunter(root)
                self._send_json(app.daily_report())
                return
            if path == "/api/strategies":
                app = WorkHunter(root)
                self._send_json(app.list_strategies())
                return
            if path == "/api/strategy/report":
                query = parse_qs(parsed.query)
                app = WorkHunter(root)
                self._send_json(app.strategy_report(_str_arg(query, "name") or "active-profile"))
                return
            if path == "/api/applications/export":
                query = parse_qs(parsed.query)
                app = WorkHunter(root)
                payload_text = app.export_applications(format=_str_arg(query, "format") or "jsonl")
                self._send_text(payload_text, content_type="application/json; charset=utf-8")
                return
            if path == "/api/report/export":
                query = parse_qs(parsed.query)
                app = WorkHunter(root)
                fmt = _str_arg(query, "format") or "json"
                content_type = "text/markdown; charset=utf-8" if fmt == "md" else "application/json; charset=utf-8"
                self._send_text(app.export_report(since=_str_arg(query, "since") or "", format=fmt), content_type=content_type)
                return
            if path == "/api/sources":
                app = WorkHunter(root)
                self._send_json(app.storage.list_sources())
                return
            if path == "/api/source-capabilities":
                app = WorkHunter(root)
                self._send_json(app.source_capabilities())
                return
            if path == "/api/doctor":
                app = WorkHunter(root)
                self._send_json(app.doctor())
                return
            if path == "/api/config":
                app = WorkHunter(root)
                self._send_json(mask_secrets(app.config))
                return
            if path == "/api/profile":
                app = WorkHunter(root)
                self._send_json(app.active_profile_info())
                return
            if path == "/api/about":
                app = WorkHunter(root)
                self._send_json(app.config.get("about", {}))
                return
            if path == "/api/stats":
                app = WorkHunter(root)
                jobs = app.list_jobs(limit=1000000)
                stats = _compute_stats(jobs, app.storage)
                self._send_json(stats)
                return
            if path.startswith("/"):
                self._send_static(path.lstrip("/"))
                return
            self._send_json({"error": "not_found"}, HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            body = self._read_json()
            app = WorkHunter(root)
            try:
                autopilot_route = HH_AUTOPILOT_MUTATION_ROUTES.get(path)
                if autopilot_route is not None:
                    query = parse_qs(parsed.query, keep_blank_values=True)
                    result = _dispatch_hh_autopilot_mutation(
                        app,
                        autopilot_route,
                        body,
                        query,
                    )
                    self._send_json(mask_secrets(result))
                    return
                if path == "/api/sync":
                    sync_result = app.sync_sources(
                        sources=body.get("sources"),
                        limit=body.get("limit"),
                    )
                    if body.get("score", True):
                        sync_result["scored"] = app.score_jobs()
                    self._send_json(sync_result)
                    return
                if path == "/api/score":
                    self._send_json({"scored": app.score_jobs(limit=body.get("limit", 10000))})
                    return
                if path == "/api/hh/resumes/sync":
                    self._send_json(app.sync_hh_resumes())
                    return
                if path == "/api/hh/token/refresh":
                    self._send_json(mask_secrets(app.refresh_hh_token()))
                    return
                if path == "/api/hh/resumes/update":
                    self._send_json(
                        app.update_hh_resumes(
                            confirm=is_literal_confirmation(body.get("confirm")),
                        )
                    )
                    return
                if path == "/api/hh/resume-template/preview":
                    self._send_json(
                        app.preview_hh_resume_template(
                            str(body.get("template") or ""),
                            context=dict(body.get("context") or {}),
                        )
                    )
                    return
                if path == "/api/hh/batch-matrix":
                    self._send_json(app.build_hh_batch_preset_matrix(dict(body.get("matrix") or body)))
                    return
                if path == "/api/hh/negotiations/sync":
                    self._send_json(app.sync_hh_negotiations(status=str(body.get("status") or "active")))
                    return
                if path == "/api/hh/reply/plan":
                    self._send_json(
                        app.plan_hh_reply(
                            negotiation_id=str(body.get("negotiation_id") or ""),
                            template=str(body.get("template") or ""),
                            status=str(body.get("status") or "active"),
                            delay_minutes=int(body.get("delay_minutes") or 0),
                        )
                    )
                    return
                if path == "/api/hh/reply/confirm":
                    self._send_json(
                        app.confirm_hh_reply(
                            int(body.get("plan_id") or 0),
                            confirm=is_literal_confirmation(body.get("confirm")),
                        )
                    )
                    return
                if path == "/api/hh/skipped/clear":
                    self._send_json(app.clear_hh_skipped_vacancies())
                    return
                if path == "/api/strategy/run":
                    self._send_json(
                        app.run_strategy(
                            str(body.get("name") or "active-profile"),
                            dry_run=bool(body.get("dry_run", True))
                            or not is_literal_confirmation(body.get("confirm")),
                            confirm=is_literal_confirmation(body.get("confirm")),
                            resume_id=str(body.get("resume_id") or "") or None,
                        )
                    )
                    return
                if path == "/api/import/jobs":
                    self._send_json(app.import_jobs_file(str(body.get("path") or "")))
                    return
                if path == "/api/hh/call":
                    self._send_json(
                        app.hh_call_api(
                            str(body.get("method") or "GET"),
                            str(body.get("path") or "/"),
                            data=body.get("data"),
                            confirm=is_literal_confirmation(body.get("confirm")),
                        )
                    )
                    return
                if path == "/api/hh/lab/call":
                    self._send_json(
                        app.hh_api_lab_call(
                            method=str(body.get("method") or "GET"),
                            path=str(body.get("path") or "/me"),
                            params=body.get("params"),
                            body=body.get("body"),
                            quick=str(body.get("quick") or ""),
                            confirm=is_literal_confirmation(body.get("confirm")),
                        )
                    )
                    return
                if path == "/api/hh/lab/snippets":
                    self._send_json(
                        app.save_hh_api_lab_snippet(
                            name=str(body.get("name") or ""),
                            method=str(body.get("method") or "GET"),
                            path=str(body.get("path") or "/me"),
                            params=body.get("params"),
                            body=body.get("body"),
                        )
                    )
                    return
                if path == "/api/hh/lab/snippets/delete":
                    self._send_json(app.delete_hh_api_lab_snippet(str(body.get("name") or "")))
                    return
                if path == "/api/agent/run":
                    self._send_json(
                        app.run_hh_agent_operation(
                            str(body.get("operation") or "digest"),
                            params=dict(body.get("params") or {}),
                        )
                    )
                    return
                if path == "/api/agent/events/scan":
                    raw_messages = body.get("messages")
                    messages = raw_messages if isinstance(raw_messages, list) else None
                    self._send_json(
                        app.scan_hh_agent_events(
                            messages=messages,
                            status=str(body.get("status") or "active"),
                            now=str(body.get("now") or "") or None,
                            limit=_optional_int(body.get("limit")),
                        )
                    )
                    return
                if path == "/api/agent/pause":
                    self._send_json(app.pause_hh_agent(reason=str(body.get("reason") or "manual")))
                    return
                if path == "/api/agent/resume":
                    self._send_json(app.resume_hh_agent(reason=str(body.get("reason") or "manual")))
                    return
                if path.startswith("/api/cancel/"):
                    operation_id = _path_int(path, "/api/cancel/")
                    self._send_json(
                        app.cancel_hh_agent_operation(
                            operation_id,
                            reason=str(body.get("reason") or "cancelled"),
                        )
                    )
                    return
                if path.startswith("/api/agent/cancel/"):
                    operation_id = _path_int(path, "/api/agent/cancel/")
                    self._send_json(
                        app.cancel_hh_agent_operation(
                            operation_id,
                            reason=str(body.get("reason") or "cancelled"),
                        )
                    )
                    return
                approval_action = _approval_action(path)
                if approval_action is not None:
                    message_id, action = approval_action
                    if action == "approve":
                        self._send_json(
                            app.approve_hh_approval(message_id, reason=str(body.get("reason") or "approved"))
                        )
                        return
                    if action == "reject":
                        self._send_json(
                            app.reject_hh_approval(message_id, reason=str(body.get("reason") or "rejected"))
                        )
                        return
                    if action == "modify":
                        self._send_json(
                            app.modify_hh_approval(
                                message_id,
                                instruction=str(body.get("instruction") or ""),
                                payload_patch=dict(body.get("payload_patch") or {}),
                            )
                        )
                        return
                    if action == "flag":
                        self._send_json(
                            app.flag_hh_approval(message_id, reason=str(body.get("reason") or "flagged"))
                        )
                        return
                    raise ValueError(f"Unsupported approval action: {action}")
                if path in {"/api/agent/templates", "/api/templates"}:
                    self._send_json(
                        app.save_hh_letter_template(
                            name=str(body.get("name") or ""),
                            body=str(body.get("body") or ""),
                        )
                    )
                    return
                if path in {"/api/agent/templates/delete", "/api/templates/delete"}:
                    self._send_json(app.delete_hh_letter_template(str(body.get("name") or "")))
                    return
                if path in {"/api/agent/blacklist", "/api/blacklist"}:
                    self._send_json(
                        app.save_hh_employer_blacklist(
                            employer_id=str(body.get("employer_id") or ""),
                            employer_name=str(body.get("employer_name") or ""),
                            reason=str(body.get("reason") or ""),
                        )
                    )
                    return
                if path in {"/api/agent/blacklist/delete", "/api/blacklist/delete"}:
                    self._send_json(app.delete_hh_employer_blacklist(str(body.get("employer_id") or "")))
                    return
                if path == "/api/hh/presets":
                    self._send_json(
                        app.save_hh_campaign_preset(
                            str(body.get("name") or ""),
                            dict(body.get("params") or {}),
                        )
                    )
                    return
                if path == "/api/hh/campaigns/plan":
                    self._send_json(
                        app.plan_hh_campaign(
                            limit=int(body.get("limit", 100)),
                            min_score=int(body.get("min_score", 0)),
                            skip_tests=bool(body.get("skip_tests", False)),
                            resume_id=body.get("resume_id"),
                        )
                    )
                    return
                if path.startswith("/api/hh/campaigns/") and path.endswith("/confirm"):
                    run_id = _path_int(path.removesuffix("/confirm"), "/api/hh/campaigns/")
                    self._send_json(
                        app.confirm_hh_campaign(
                            run_id,
                            confirm=is_literal_confirmation(body.get("confirm")),
                        )
                    )
                    return
                if path == "/api/chat":
                    messages = body.get("messages", [])
                    job_id = body.get("job_id")
                    chat_result = app.chat(messages, job_id=job_id)
                    self._send_json({"content": chat_result})
                    return
                if path == "/api/config/secret/clear":
                    self._send_json(
                        app.clear_config_secret(
                            str(body.get("path") or ""),
                            confirm=body.get("confirm"),
                        )
                    )
                    return
                if path == "/api/config":
                    self._send_json(app.update_config_from_client(body))
                    return
                if path.startswith("/api/jobs/") and path.endswith("/status"):
                    job_id = _path_int(path.removesuffix("/status"), "/api/jobs/")
                    app.mark_job(job_id, str(body.get("status") or "saved"), str(body.get("note") or ""))
                    self._send_json({"status": "ok"})
                    return
                if path.startswith("/api/jobs/") and path.endswith("/letter"):
                    job_id = _path_int(path.removesuffix("/letter"), "/api/jobs/")
                    self._send_json(app.prepare_letter(job_id).to_dict())
                    return
                if path.startswith("/api/jobs/") and path.endswith("/letter-ai"):
                    job_id = _path_int(path.removesuffix("/letter-ai"), "/api/jobs/")
                    self._send_json(app.prepare_letter_ai(job_id).to_dict())
                    return
                if path == "/api/profile/switch":
                    profile_id = str(body.get("profile", ""))
                    new_profile = app.switch_profile(profile_id)
                    self._send_json({"active": profile_id, "data": new_profile})
                    return
                if path == "/api/profile":
                    updated = app.update_profile(body)
                    self._send_json(updated)
                    return
                if path.startswith("/api/jobs/") and path.endswith("/apply-hh"):
                    job_id = _path_int(path.removesuffix("/apply-hh"), "/api/jobs/")
                    self._send_json(
                        app.apply_hh(
                            job_id,
                            resume_id=body.get("resume_id"),
                            dry_run=body.get("dry_run", True),
                        )
                    )
                    return
                if path.startswith("/api/jobs/") and path.endswith("/apply-plan"):
                    job_id = _path_int(path.removesuffix("/apply-plan"), "/api/jobs/")
                    self._send_json(
                        app.prepare_apply_plan(
                            job_id,
                            resume_id=body.get("resume_id"),
                            letter=body.get("letter"),
                        )
                    )
                    return
                if path.startswith("/api/jobs/") and path.endswith("/confirm-apply"):
                    job_id = _path_int(path.removesuffix("/confirm-apply"), "/api/jobs/")
                    self._send_json(
                        app.confirm_apply(
                            job_id,
                            resume_id=body.get("resume_id"),
                            letter=body.get("letter"),
                            confirm=is_literal_confirmation(body.get("confirm")),
                        )
                    )
                    return
                if path.startswith("/api/jobs/") and path.endswith("/resume-tips"):
                    job_id = _path_int(path.removesuffix("/resume-tips"), "/api/jobs/")
                    self._send_json({"content": app.resume_tips(job_id)})
                    return
                if path.startswith("/api/jobs/") and path.endswith("/ats-resume"):
                    job_id = _path_int(path.removesuffix("/ats-resume"), "/api/jobs/")
                    self._send_json({"content": app.ats_resume(job_id)})
                    return
                if path.startswith("/api/jobs/") and path.endswith("/summarize"):
                    job_id = _path_int(path.removesuffix("/summarize"), "/api/jobs/")
                    self._send_json({"summary": app.summarize_job(job_id)})
                    return
                if path.startswith("/api/jobs/") and path.endswith("/ai-fit"):
                    job_id = _path_int(path.removesuffix("/ai-fit"), "/api/jobs/")
                    self._send_json(app.ai_fit(job_id))
                    return
                if path.startswith("/api/jobs/") and path.endswith("/interview-questions"):
                    job_id = _path_int(path.removesuffix("/interview-questions"), "/api/jobs/")
                    self._send_json({"content": app.interview_questions(job_id)})
                    return
                if path.startswith("/api/jobs/") and path.endswith("/pitch"):
                    job_id = _path_int(path.removesuffix("/pitch"), "/api/jobs/")
                    self._send_json({"content": app.experience_pitch(job_id)})
                    return
                if path.startswith("/api/jobs/") and path.endswith("/fetch-full"):
                    job_id = _path_int(path.removesuffix("/fetch-full"), "/api/jobs/")
                    self._send_json(app.fetch_full_description(job_id))
                    return
                if path.startswith("/api/jobs/") and path.endswith("/note"):
                    job_id = _path_int(path.removesuffix("/note"), "/api/jobs/")
                    note_body = body.get("body", "")
                    app.storage.save_note(job_id, note_body)
                    self._send_json({"body": app.storage.get_note(job_id)})
                    return
                if path.startswith("/api/jobs/") and path.endswith("/apply"):
                    job_id = _path_int(path.removesuffix("/apply"), "/api/jobs/")
                    app.storage.save_application(job_id, str(body.get("status", "applied")), str(body.get("notes", "")))
                    self._send_json({"status": "ok"})
                    return
                if path == "/api/jobs/search-ai":
                    self._send_json(app.search_by_description(
                        str(body.get("query", "")),
                        limit=body.get("limit", 20),
                    ))
                    return
                if path == "/api/ats-audit":
                    self._send_json({
                        "content": app.ats_audit(
                            str(body.get("resume_text", "")),
                            job_id=body.get("job_id"),
                        )
                    })
                    return
                if path == "/api/resumes":
                    ats_score = body.get("ats_score")
                    if ats_score is not None and (
                        isinstance(ats_score, bool)
                        or not isinstance(ats_score, int)
                        or not 0 <= ats_score <= 100
                    ):
                        raise ValueError(
                            "ats_score must be an integer between 0 and 100 or null"
                        )
                    resume = Resume(
                        name=body.get("name", ""),
                        body=body.get("body", ""),
                        profile_id=body.get("profile_id", "default"),
                        is_active=body.get("is_active", False),
                        ats_score=ats_score,
                    )
                    if body.get("id"):
                        resume.id = int(body["id"])
                        if resume.id <= 0:
                            raise ValueError("resume id must be a positive integer")
                        if app.storage.get_resume(resume.id) is None:
                            self._send_json(
                                {"error": "not_found"}, HTTPStatus.NOT_FOUND
                            )
                            return
                    saved_id = app.storage.save_resume(resume)
                    self._send_json({"id": saved_id})
                    return
                if path == "/api/resumes/ats-score":
                    resume_text = body.get("resume_text", "")
                    ats_result = app.ats_score_resume(resume_text)
                    self._send_json(ats_result)
                    return
                if path.startswith("/api/resumes/") and path.endswith("/delete"):
                    resume_id = _path_int(path.removesuffix("/delete"), "/api/resumes/")
                    app.storage.delete_resume(resume_id)
                    self._send_json({"status": "ok"})
                    return
                if path.startswith("/api/resumes/") and path.endswith("/activate"):
                    resume_id = _path_int(path.removesuffix("/activate"), "/api/resumes/")
                    app.storage.set_active_resume(resume_id)
                    self._send_json({"status": "ok"})
                    return
                if path == "/api/events":
                    event = CalendarEvent(
                        job_id=body.get("job_id"),
                        title=body.get("title", ""),
                        event_type=body.get("event_type", "interview"),
                        event_date=body.get("event_date", ""),
                        notes=body.get("notes", ""),
                    )
                    if body.get("id"):
                        event.id = body["id"]
                    saved_id = app.storage.save_event(event)
                    self._send_json({"id": saved_id})
                    return
                if path.startswith("/api/events/") and path.endswith("/delete"):
                    event_id = _path_int(path.removesuffix("/delete"), "/api/events/")
                    app.storage.delete_event(event_id)
                    self._send_json({"status": "ok"})
                    return
                if path == "/api/saved-searches":
                    search = SavedSearch(
                        name=body.get("name", ""),
                        query=body.get("query", ""),
                        filters_json=body.get("filters_json", "{}"),
                        alert_enabled=body.get("alert_enabled", False),
                    )
                    app.storage.save_search(search)
                    self._send_json({"status": "ok"})
                    return
                if path.startswith("/api/saved-searches/") and path.endswith("/delete"):
                    search_id = _path_int(path.removesuffix("/delete"), "/api/saved-searches/")
                    app.storage.delete_search(search_id)
                    self._send_json({"status": "ok"})
                    return
                if path == "/api/jobs/bulk":
                    job_ids = body.get("job_ids", [])
                    action = body.get("action", "")
                    for job_id in job_ids:
                        app.storage.set_status(job_id, action)
                        app.storage.record_event(job_id, action)
                    self._send_json({"processed": len(job_ids)})
                    return
                if path == "/api/jobs/classify-batch":
                    job_ids = body.get("job_ids", [])
                    batch_result = app.classify_jobs_batch(job_ids)
                    self._send_json(batch_result)
                    return
                if path == "/api/market-trends":
                    limit = body.get("limit", 50)
                    trend_result = app.market_trends(limit)
                    self._send_json({"content": trend_result})
                    return
                if path == "/api/behavior/suggest":
                    behavior_result = app.behavior_suggest()
                    self._send_json({"content": behavior_result})
                    return
                if path.startswith("/api/jobs/") and path.endswith("/parse-structure"):
                    job_id = _path_int(path.removesuffix("/parse-structure"), "/api/jobs/")
                    structure_result = app.parse_job_structure(job_id)
                    self._send_json(structure_result)
                    return
                if path.startswith("/api/jobs/") and path.endswith("/gap-analysis"):
                    job_id = _path_int(path.removesuffix("/gap-analysis"), "/api/jobs/")
                    resume_id = body.get("resume_id", 0)
                    gap_result = app.gap_analysis(resume_id, job_id)
                    self._send_json({"content": gap_result})
                    return
                if path.startswith("/api/jobs/") and path.endswith("/smart-classify"):
                    job_id = _path_int(path.removesuffix("/smart-classify"), "/api/jobs/")
                    classification_result = app.smart_classify(job_id)
                    self._send_json(classification_result)
                    return
                if path.startswith("/api/jobs/") and path.endswith("/interview-prep"):
                    job_id = _path_int(path.removesuffix("/interview-prep"), "/api/jobs/")
                    stage = body.get("stage", "tech")
                    interview_result = app.interview_stage_prep(job_id, stage)
                    self._send_json({"content": interview_result})
                    return
                if path.startswith("/api/jobs/") and path.endswith("/record-event"):
                    job_id = _path_int(path.removesuffix("/record-event"), "/api/jobs/")
                    action = body.get("action", "viewed")
                    app.storage.record_event(job_id, action)
                    self._send_json({"status": "ok"})
                    return
            except Exception as exc:
                payload = {"error": str(exc)}
                if path in HH_AUTOPILOT_MUTATION_ROUTES:
                    payload["status"] = "blocked"
                self._send_json(payload, HTTPStatus.BAD_REQUEST)
                return
            self._send_json({"error": "not_found"}, HTTPStatus.NOT_FOUND)

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or "0")
            if length <= 0:
                return {}
            raw = self.rfile.read(length).decode("utf-8")
            return json.loads(raw or "{}")

        def _send_json(self, data: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
            payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _send_text(
            self,
            text: str,
            *,
            status: HTTPStatus = HTTPStatus.OK,
            content_type: str = "text/plain; charset=utf-8",
        ) -> None:
            payload = text.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _send_static(self, relative_path: str) -> None:
            path = (STATIC_DIR / relative_path).resolve()
            if not str(path).startswith(str(STATIC_DIR.resolve())) or not path.exists():
                self._send_json({"error": "not_found"}, HTTPStatus.NOT_FOUND)
                return
            content = path.read_bytes()
            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

    return WorkHunterHandler


def _single_query_arg(
    query: dict[str, list[str]],
    name: str,
) -> str | None:
    values = query.get(name, [])
    if len(values) > 1:
        raise ValueError(f"query parameter '{name}' must be supplied once")
    if not values:
        return None
    value = values[0].strip()
    return value or None


def _request_account(
    body: dict[str, Any],
    query: dict[str, list[str]],
) -> str | None:
    query_account = _single_query_arg(query, "account")
    raw_account = body.get("account")
    if raw_account is None:
        account = query_account
    else:
        if not isinstance(raw_account, str) or not raw_account.strip():
            raise ValueError("account must be a non-empty string")
        account = raw_account.strip()
        if query_account is not None and query_account.casefold() != account.casefold():
            raise ValueError("query and body account disagree")
    return account


def _positive_payload_int(body: dict[str, Any], name: str) -> int:
    value = body.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _dispatch_hh_autopilot_get(
    app: WorkHunter,
    action: str,
    query: dict[str, list[str]],
) -> dict[str, Any]:
    account = _single_query_arg(query, "account")
    if action == "config":
        return app.hh_autopilot_config(account=account)
    if action == "validate":
        return app.validate_hh_autopilot(account=account)
    if action == "status":
        return app.hh_autopilot_status(account=account)
    if action == "history":
        limit = int(_single_query_arg(query, "limit") or "100")
        return app.hh_autopilot_history(
            account=account,
            vacancy_id=_single_query_arg(query, "vacancy_id"),
            limit=limit,
            offset=int(_single_query_arg(query, "offset") or "0"),
        )
    if action == "challenges":
        limit = int(_single_query_arg(query, "limit") or "100")
        return app.hh_autopilot_challenges(account=account, limit=limit)
    raise AssertionError(f"unsupported HH autopilot GET action: {action}")


def _dispatch_hh_autopilot_mutation(
    app: WorkHunter,
    route: _AutopilotMutationRoute,
    body: dict[str, Any],
    query: dict[str, list[str]],
) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise ValueError("JSON body must be an object")
    account = _request_account(body, query)
    all_accounts = body.get("all") is True or body.get("scope") == "all"
    global_scope = body.get("global") is True or body.get("scope") == "global"
    selected_scopes = sum((account is not None, all_accounts, global_scope))
    if selected_scopes > 1:
        raise ValueError("account, all, and global scopes are mutually exclusive")
    if all_accounts and not route.allows_all:
        raise ValueError("all-account scope is not supported for this action")
    if global_scope and not route.allows_global:
        raise ValueError("global scope is not supported for this action")
    if route.account_required and selected_scopes == 0:
        raise ValueError("an explicit account scope is required")
    if route.confirmation_required and body.get("confirm") is not True:
        raise ValueError("literal JSON confirmation true is required")

    accounts = None if all_accounts or global_scope else ([account] if account else None)
    action = route.action
    if action == "config":
        submitted = body.get("config", body)
        if submitted is body:
            submitted = {
                key: value
                for key, value in body.items()
                if key not in {"account", "scope", "all", "global", "confirm"}
            }
        if not isinstance(submitted, dict):
            raise ValueError("config must be an object")
        return app.update_hh_autopilot_config(submitted, account=account)
    if action == "enable":
        return app.enable_hh_autopilot(accounts=accounts, confirm=True)
    if action == "disable":
        return app.disable_hh_autopilot(accounts=accounts, confirm=True)
    if action == "pause":
        return app.pause_hh_autopilot(accounts=accounts)
    if action == "resume":
        return app.resume_hh_autopilot(accounts=accounts)
    if action == "stop":
        assert account is not None
        return app.stop_hh_autopilot(
            account=account,
            run_id=_positive_payload_int(body, "run_id"),
        )
    if action == "kill-switch":
        return app.kill_hh_autopilot(
            accounts=accounts,
            global_scope=global_scope,
            confirm=True,
        )
    if action == "clear-kill-switch":
        return app.clear_hh_autopilot_kill_switch(
            accounts=accounts,
            global_scope=global_scope,
            confirm=True,
        )
    if action == "shadow":
        assert account is not None
        return app.shadow_hh_autopilot(
            account=account,
            resume_id=_optional_nonempty_text(body.get("resume_id"), "resume_id"),
            preset=_optional_nonempty_text(body.get("preset"), "preset"),
        )
    if action == "canary":
        assert account is not None
        resume_id = _optional_nonempty_text(body.get("resume_id"), "resume_id")
        vacancy_id = _optional_nonempty_text(body.get("vacancy_id"), "vacancy_id")
        if resume_id is None or vacancy_id is None:
            raise ValueError("resume_id and vacancy_id are required")
        return app.canary_hh_autopilot(
            account=account,
            resume_id=resume_id,
            vacancy_id=vacancy_id,
            confirm=True,
        )
    if action == "run-now":
        return app.run_hh_autopilot(accounts=accounts)
    if action == "recover-now":
        return app.recover_hh_autopilot(account=account)
    if action == "retry":
        assert account is not None
        return app.retry_hh_autopilot(
            account=account,
            item_id=_positive_payload_int(body, "item_id"),
        )
    if action == "resolve-challenge":
        assert account is not None
        challenge_action = _optional_nonempty_text(body.get("action"), "action")
        if challenge_action not in {
            "completed",
            "dismissed",
            "confirmed_applied",
            "confirmed_not_applied_retry",
            "confirmed_not_applied_skip",
            "retry_reconciliation",
            "auth_restored",
        }:
            raise ValueError("unsupported challenge action")
        return app.resolve_hh_autopilot_challenge(
            account=account,
            challenge_id=_positive_payload_int(body, "challenge_id"),
            action=challenge_action,
        )
    raise AssertionError(f"unsupported HH autopilot mutation action: {action}")


def _optional_nonempty_text(value: Any, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _path_int(path: str, prefix: str) -> int:
    return int(path.removeprefix(prefix).strip("/"))


def _int_arg(query: dict[str, list[str]], name: str, default: int) -> int:
    value = query.get(name, [str(default)])[0]
    return int(value)


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def _optional_int_arg(query: dict[str, list[str]], name: str) -> int | None:
    values = query.get(name)
    if not values or values[0] == "":
        return None
    return int(values[0])


def _str_arg(query: dict[str, list[str]], name: str) -> str | None:
    value = query.get(name, [None])[0]
    return value or None


def _bool_arg(query: dict[str, list[str]], name: str, default: bool) -> bool:
    value = query.get(name, [None])[0]
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _approval_action(path: str) -> tuple[int, str] | None:
    parts = path.strip("/").split("/")
    if len(parts) == 4 and parts[:2] == ["api", "approvals"]:
        return int(parts[2]), parts[3]
    if len(parts) == 5 and parts[:3] == ["api", "agent", "approvals"]:
        return int(parts[3]), parts[4]
    return None


def _job_json(job: Any, storage: Any, *, note_preview_chars: int = 120) -> dict[str, Any]:
    payload = job.to_dict()
    note = storage.get_note(int(job.id or 0))
    payload["note_preview"] = note[:note_preview_chars]
    return payload


def _application_stats(by_status: dict[str, int]) -> tuple[int, list[dict[str, object]]]:
    funnel = [
        {"status": status, "count": by_status.get(status, 0)}
        for status in APPLICATION_FUNNEL_STAGES
    ]
    return sum(cast(int, item["count"]) for item in funnel), funnel


def _compute_stats(jobs: list[Any], storage: Any) -> dict[str, Any]:
    total = len(jobs)
    by_source: dict[str, int] = {}
    by_status: dict[str, int] = {}
    score_buckets: dict[str, int] = {
        "0-20": 0, "21-40": 0, "41-60": 0, "61-80": 0, "81-100": 0,
    }
    for job in jobs:
        by_source[job.source] = by_source.get(job.source, 0) + 1
        status = job.status or "new"
        by_status[status] = by_status.get(status, 0) + 1
        s = job.score.total_score if job.score else 0
        if s <= 20:
            score_buckets["0-20"] += 1
        elif s <= 40:
            score_buckets["21-40"] += 1
        elif s <= 60:
            score_buckets["41-60"] += 1
        elif s <= 80:
            score_buckets["61-80"] += 1
        else:
            score_buckets["81-100"] += 1
    total_applications, application_funnel = _application_stats(by_status)
    return {
        "total_jobs": total,
        "by_source": by_source,
        "by_status": by_status,
        "score_distribution": score_buckets,
        "total_applications": total_applications,
        "application_funnel": application_funnel,
    }
