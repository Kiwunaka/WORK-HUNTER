from __future__ import annotations

import json
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from ..config import mask_secrets
from ..models import CalendarEvent, Resume, SavedSearch
from ..security.redaction import redact_secrets
from ..resume_engine import import_resume_file
from ..services import WorkHunter


STATIC_DIR = Path(__file__).parent / "static"


def run_server(root: str | Path | None = None, host: str = "127.0.0.1", port: int = 8787) -> None:
    handler = make_handler(Path(root) if root is not None else Path.cwd())
    server = ThreadingHTTPServer((host, port), handler)
    print(f"Work Hunter UI: http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def make_handler(root: Path):
    class WorkHunterHandler(BaseHTTPRequestHandler):
        server_version = "WorkHunterHTTP/0.1"

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            if path == "/":
                self._send_static("index.html")
                return
            if path == "/api/init/status":
                app = WorkHunter(root)
                self._send_json(app.init_report(check=True, with_ai=True, with_browser=True))
                return
            if path == "/api/doctor":
                app = WorkHunter(root)
                self._send_json(app.doctor_report())
                return
            if path == "/api/ai/status":
                app = WorkHunter(root)
                self._send_json(app.ai_status())
                return
            if path == "/api/ai/runs":
                query = parse_qs(parsed.query)
                app = WorkHunter(root)
                self._send_json(app.storage.list_ai_runs(limit=_int_arg(query, "limit", 20)))
                return
            if path == "/api/source-setup/guide":
                query = parse_qs(parsed.query)
                app = WorkHunter(root)
                self._send_json(
                    app.source_setup_guide(
                        _str_arg(query, "source") or "hh",
                        level=_int_arg(query, "level", 5),
                    )
                )
                return
            if path == "/api/onboarding/status":
                app = WorkHunter(root)
                self._send_json(app.candidate_completeness())
                return
            if path == "/api/onboarding/questions":
                app = WorkHunter(root)
                self._send_json(app.onboarding_questions())
                return
            if path == "/api/candidate/profile":
                app = WorkHunter(root)
                self._send_json(app.active_profile_info())
                return
            if path == "/api/candidate/map":
                app = WorkHunter(root)
                self._send_json(app.candidate_map())
                return
            if path == "/api/candidate/facts":
                app = WorkHunter(root)
                self._send_json(app.candidate_facts())
                return
            if path == "/api/candidate/completeness":
                app = WorkHunter(root)
                self._send_json(app.candidate_completeness())
                return
            if path.startswith("/api/replay/jobs/") and path.endswith("/export"):
                query = parse_qs(parsed.query)
                job_id = _path_int(path.removesuffix("/export"), "/api/replay/jobs/")
                app = WorkHunter(root)
                self._send_text(
                    app.export_replay_markdown(
                        job_id=job_id,
                        source=_str_arg(query, "source"),
                        event_type=_str_arg(query, "event_type"),
                    ),
                    content_type="text/markdown; charset=utf-8",
                )
                return
            if path.startswith("/api/replay/runs/") and path.endswith("/export"):
                query = parse_qs(parsed.query)
                run_id = _path_int(path.removesuffix("/export"), "/api/replay/runs/")
                app = WorkHunter(root)
                self._send_text(
                    app.export_replay_markdown(
                        run_id=run_id,
                        source=_str_arg(query, "source"),
                        event_type=_str_arg(query, "event_type"),
                    ),
                    content_type="text/markdown; charset=utf-8",
                )
                return
            if path.startswith("/api/replay/events/") and path.endswith("/screenshot"):
                query = parse_qs(parsed.query)
                event_id = _path_int(path.removesuffix("/screenshot"), "/api/replay/events/")
                app = WorkHunter(root)
                event = app.storage.get_replay_event(event_id)
                screenshot_name = _str_arg(query, "name") or "screenshot"
                screenshot_path = _replay_screenshot_path(root, app, event, screenshot_name)
                if screenshot_path is None:
                    self._send_json({"error": "not_found"}, HTTPStatus.NOT_FOUND)
                    return
                content = screenshot_path.read_bytes()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", mimetypes.guess_type(screenshot_path.name)[0] or "application/octet-stream")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
                return
            if path.startswith("/api/replay/jobs/"):
                query = parse_qs(parsed.query)
                job_id = _path_int(path, "/api/replay/jobs/")
                app = WorkHunter(root)
                self._send_json(
                    app.replay_for_job(
                        job_id,
                        source=_str_arg(query, "source"),
                        event_type=_str_arg(query, "event_type"),
                    )
                )
                return
            if path == "/api/replay/runs":
                query = parse_qs(parsed.query)
                app = WorkHunter(root)
                runs = app.storage.list_hh_campaign_runs(limit=_int_arg(query, "limit", 20))
                self._send_json([run.to_dict() for run in runs])
                return
            if path.startswith("/api/replay/runs/"):
                query = parse_qs(parsed.query)
                run_id = _path_int(path, "/api/replay/runs/")
                app = WorkHunter(root)
                self._send_json(
                    app.replay_for_run(
                        run_id,
                        source=_str_arg(query, "source"),
                        event_type=_str_arg(query, "event_type"),
                    )
                )
                return
            if path == "/api/source-capabilities":
                app = WorkHunter(root)
                self._send_json(app.source_capabilities())
                return
            if path == "/api/source-status":
                app = WorkHunter(root)
                self._send_json(app.source_capabilities())
                return
            if path in {"/api/sources/status", "/api/sources/capabilities"}:
                app = WorkHunter(root)
                self._send_json(app.source_capabilities())
                return
            if path == "/api/security/status":
                app = WorkHunter(root)
                self._send_json(app.security_status())
                return
            if path == "/api/browser/status":
                query = parse_qs(parsed.query)
                app = WorkHunter(root)
                self._send_json(app.browser_lab_status(_str_arg(query, "source") or "getmatch"))
                return
            if path == "/api/browser-lab/status":
                query = parse_qs(parsed.query)
                app = WorkHunter(root)
                self._send_json(app.browser_lab_status(_str_arg(query, "source") or "getmatch"))
                return
            if path == "/api/campaigns/presets":
                app = WorkHunter(root)
                self._send_json(app.list_hh_campaign_presets())
                return
            if path == "/api/campaigns":
                query = parse_qs(parsed.query)
                app = WorkHunter(root)
                runs = app.storage.list_hh_campaign_runs(limit=_int_arg(query, "limit", 20))
                self._send_json([run.to_dict() for run in runs])
                return
            if path.startswith("/api/campaigns/") and path.endswith("/timeline"):
                run_id = _path_int(path.removesuffix("/timeline"), "/api/campaigns/")
                app = WorkHunter(root)
                self._send_json(app.replay_for_run(run_id))
                return
            if path.startswith("/api/campaigns/"):
                run_id = _path_int(path, "/api/campaigns/")
                app = WorkHunter(root)
                self._send_json(_campaign_review_payload(app, run_id))
                return
            if path == "/api/hh/campaigns":
                query = parse_qs(parsed.query)
                app = WorkHunter(root)
                runs = app.storage.list_hh_campaign_runs(limit=_int_arg(query, "limit", 20))
                self._send_json([run.to_dict() for run in runs])
                return
            if path.startswith("/api/hh/campaigns/"):
                run_id = _path_int(path, "/api/hh/campaigns/")
                app = WorkHunter(root)
                run = app.storage.get_hh_campaign_run(run_id)
                items = []
                for item in app.storage.list_hh_campaign_items(run_id):
                    payload = item.to_dict()
                    job = app.storage.get_job(item.job_id)
                    payload["job"] = job.to_dict() if job else None
                    items.append(payload)
                self._send_json({"run": run.to_dict() if run else None, "items": items})
                return
            if path in {"/api/jobs", "/api/inbox"}:
                query = parse_qs(parsed.query)
                app = WorkHunter(root)
                jobs = app.list_jobs(
                    limit=_int_arg(query, "limit", 100),
                    source=_str_arg(query, "source"),
                    status=_str_arg(query, "status"),
                    min_score=_optional_int_arg(query, "min_score"),
                )
                self._send_json([job.to_dict() for job in jobs])
                return
            if path == "/api/jobs/export":
                query = parse_qs(parsed.query)
                app = WorkHunter(root)
                csv_data = app.export_jobs(
                    status=_str_arg(query, "status"),
                    source=_str_arg(query, "source"),
                    format="csv",
                )
                payload = csv_data.encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/csv; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
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
                payload = job.to_dict()
                letter = app.latest_letter(job_id)
                payload["latest_letter"] = letter.to_dict() if letter else None
                self._send_json(payload)
                return
            if path == "/api/resumes":
                app = WorkHunter(root)
                resumes = app.storage.list_resumes()
                self._send_json([r.to_dict() for r in resumes])
                return
            if path == "/api/resumes/assets":
                app = WorkHunter(root)
                self._send_json({"items": [r.to_dict() for r in app.storage.list_resumes()]})
                return
            if path == "/api/resumes/variants":
                app = WorkHunter(root)
                self._send_json(app.storage.list_resume_variants(profile_id=str(app.config.get("profile") or "default")))
                return
            if path.startswith("/api/resumes/variants/") and path.endswith("/diff"):
                variant_id = _path_int(path.removesuffix("/diff"), "/api/resumes/variants/")
                app = WorkHunter(root)
                variant = app.storage.get_resume_variant(variant_id)
                if variant is None:
                    self._send_json({"error": "not_found"}, HTTPStatus.NOT_FOUND)
                    return
                policy = dict(variant.get("policy_result") or {})
                self._send_json({"id": variant_id, "diff": str(policy.get("diff") or ""), "variant": variant})
                return
            if path.startswith("/api/applications/"):
                pack_id = _path_int(path, "/api/applications/")
                app = WorkHunter(root)
                pack = app.storage.get_application_pack(pack_id)
                if pack is None:
                    self._send_json({"error": "not_found"}, HTTPStatus.NOT_FOUND)
                    return
                self._send_json(pack)
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
                jobs = app.storage.get_ghost_jobs(days=days)
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
            if path == "/api/sources":
                app = WorkHunter(root)
                self._send_json(app.storage.list_sources())
                return
            if path == "/api/config":
                app = WorkHunter(root)
                self._send_json(mask_secrets(app.config))
                return
            if path == "/api/profile":
                app = WorkHunter(root)
                self._send_json(app.active_profile_info())
                return
            if path.startswith("/api/pipeline/jobs/"):
                app = WorkHunter(root)
                job_id = _path_int(path, "/api/pipeline/jobs/")
                self._send_json(app.pipeline_status(job_id))
                return
            if path == "/api/about":
                app = WorkHunter(root)
                self._send_json(app.config.get("about", {}))
                return
            if path == "/api/stats":
                app = WorkHunter(root)
                jobs = app.storage.list_jobs(limit=1000000)
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
                if path == "/api/sync":
                    result = app.sync_sources(
                        sources=body.get("sources"),
                        limit=body.get("limit"),
                    )
                    if body.get("score", True):
                        result["scored"] = app.score_jobs()
                    self._send_json(result)
                    return
                if path == "/api/score":
                    self._send_json({"scored": app.score_jobs(limit=body.get("limit", 10000))})
                    return
                if path == "/api/init":
                    self._send_json(
                        app.init_report(
                            check=bool(body.get("check", True)),
                            refresh_docs=bool(body.get("refresh_docs", False)),
                            with_ai=bool(body.get("with_ai", False)),
                            with_browser=bool(body.get("with_browser", False)),
                            import_wo=body.get("import_wo"),
                            dry_run=bool(body.get("dry_run", False)),
                            force=bool(body.get("force", False)),
                        )
                    )
                    return
                if path == "/api/init/redaction-scan":
                    self._send_json(_redaction_scan_payload(body))
                    return
                if path == "/api/ai/config":
                    updated = dict(app.config)
                    ai_config = dict(updated.get("ai") or {})
                    ai_config = _deep_merge(ai_config, body)
                    updated["ai"] = ai_config
                    app.save_config(updated)
                    self._send_json(mask_secrets(app.config.get("ai") or {}))
                    return
                if path == "/api/ai/run":
                    route = str(body.get("route") or app.config.get("ai", {}).get("default_route") or "smart")
                    run_id = app.storage.start_ai_run(route, body)
                    try:
                        result = app.ai_test(
                            route=route,
                            prompt=str(body.get("prompt") or "ping"),
                            dry_run=bool(body.get("dry_run", False)),
                        )
                        app.storage.finish_ai_run(run_id, status=str(result.get("status") or "ok"), output=result)
                        self._send_json({**result, "ai_run_id": run_id})
                    except Exception as exc:
                        app.storage.finish_ai_run(run_id, status="error", output={"error": str(exc)})
                        raise
                    return
                if path == "/api/source-setup/action":
                    self._send_json(
                        app.source_setup_action(
                            str(body.get("action") or ""),
                            source=str(body.get("source") or "hh"),
                            level=int(body.get("level") or 5),
                            **{key: value for key, value in body.items() if key not in {"action", "source", "level"}},
                        )
                    )
                    return
                if path.startswith("/api/sources/") and path.endswith("/sync"):
                    source = path.removeprefix("/api/sources/").removesuffix("/sync").strip("/")
                    self._send_json(_source_sync_payload(app, source, body))
                    return
                if path.startswith("/api/sources/") and path.endswith("/test"):
                    source = path.removeprefix("/api/sources/").removesuffix("/test").strip("/")
                    report = app.source_capabilities()
                    self._send_json({"status": "ok", "source": source, "capabilities": report.get(source)})
                    return
                if path == "/api/sources/certification-matrix":
                    raw_sources = body.get("sources")
                    sources = [str(source) for source in raw_sources] if isinstance(raw_sources, list) else None
                    self._send_json(
                        app.source_certification_matrix(
                            level=int(body.get("level") or body.get("maturity_level") or 5),
                            sources=sources,
                            evidence=dict(body.get("evidence") or body.get("certification") or {}),
                        )
                    )
                    return
                if path == "/api/sources/certification-plan":
                    raw_sources = body.get("sources")
                    sources = [str(source) for source in raw_sources] if isinstance(raw_sources, list) else None
                    self._send_json(
                        app.source_certification_plan(
                            level=int(body.get("level") or body.get("maturity_level") or 5),
                            sources=sources,
                            evidence=dict(body.get("evidence") or body.get("certification") or {}),
                        )
                    )
                    return
                if path.startswith("/api/sources/") and path.endswith("/certification-audit"):
                    source = path.removeprefix("/api/sources/").removesuffix("/certification-audit").strip("/")
                    self._send_json(
                        app.source_certification_audit(
                            source,
                            level=int(body.get("level") or body.get("maturity_level") or 5),
                            evidence=dict(body.get("evidence") or body.get("certification") or {}),
                        )
                    )
                    return
                if path.startswith("/api/sources/") and path.endswith("/certification-evidence"):
                    source = path.removeprefix("/api/sources/").removesuffix("/certification-evidence").strip("/")
                    self._send_json(
                        app.record_source_certification_evidence(
                            source,
                            level=int(body.get("level") or body.get("maturity_level") or 5),
                            evidence=dict(body.get("evidence") or body.get("certification") or {}),
                        )
                    )
                    return
                if path.startswith("/api/sources/") and path.endswith("/redaction-scan"):
                    source = path.removeprefix("/api/sources/").removesuffix("/redaction-scan").strip("/")
                    raw_payload = body.get("payload")
                    payload = raw_payload if isinstance(raw_payload, dict) else {}
                    self._send_json(
                        app.record_source_redaction_scan(
                            source,
                            payload=payload,
                            text=str(body.get("text") or ""),
                            level=int(body.get("level") or body.get("maturity_level") or 5),
                        )
                    )
                    return
                if path.startswith("/api/sources/") and path.endswith("/external-apply-target"):
                    source = path.removeprefix("/api/sources/").removesuffix("/external-apply-target").strip("/")
                    raw_template = body.get("payload_template")
                    payload_template = raw_template if isinstance(raw_template, dict) else {}
                    self._send_json(
                        app.configure_source_external_apply_target(
                            source,
                            session=str(body.get("session") or ""),
                            url=str(body.get("url") or ""),
                            method=str(body.get("method") or "POST"),
                            payload_template=payload_template,
                            level=int(body.get("level") or body.get("maturity_level") or 5),
                        )
                    )
                    return
                if path.startswith("/api/sources/") and path.endswith("/external-apply-from-har"):
                    source = path.removeprefix("/api/sources/").removesuffix("/external-apply-from-har").strip("/")
                    raw_hosts = body.get("hosts") or body.get("allowed_hosts") or body.get("host") or []
                    if isinstance(raw_hosts, str):
                        hosts = {raw_hosts} if raw_hosts else None
                    elif isinstance(raw_hosts, list):
                        hosts = {str(item) for item in raw_hosts if str(item).strip()} or None
                    else:
                        hosts = None
                    self._send_json(
                        app.configure_source_external_apply_from_har(
                            source,
                            Path(str(body.get("path") or body.get("har_path") or "")),
                            allowed_hosts=hosts,
                            level=int(body.get("level") or body.get("maturity_level") or 5),
                        )
                    )
                    return
                if path.startswith("/api/sources/") and path.endswith("/certify"):
                    source = path.removeprefix("/api/sources/").removesuffix("/certify").strip("/")
                    self._send_json(
                        app.promote_source_certification(
                            source,
                            level=int(body.get("level") or body.get("maturity_level") or 5),
                            evidence=dict(body.get("evidence") or body.get("certification") or {}),
                        )
                    )
                    return
                if path.startswith("/api/sources/") and path.endswith("/promote-maturity"):
                    source = path.removeprefix("/api/sources/").removesuffix("/promote-maturity").strip("/")
                    self._send_json(_promote_source_maturity_payload(app, source, body))
                    return
                if path == "/api/init/import-wo/preview":
                    self._send_json(
                        app.init_report(
                            import_wo=str(body.get("source") or ""),
                            dry_run=True,
                        )["import_wo"]
                    )
                    return
                if path == "/api/init/import-wo":
                    self._send_json(
                        app.init_report(
                            import_wo=str(body.get("source") or ""),
                            dry_run=False,
                        )["import_wo"]
                    )
                    return
                if path == "/api/ai/test":
                    self._send_json(
                        app.ai_test(
                            route=body.get("route"),
                            prompt=str(body.get("prompt") or "ping"),
                            dry_run=bool(body.get("dry_run", False)),
                        )
                    )
                    return
                if path == "/api/onboarding/answer":
                    self._send_json(
                        app.answer_onboarding(
                            str(body.get("question_id") or body.get("id") or ""),
                            str(body.get("answer") or ""),
                            source=str(body.get("source") or "web"),
                        )
                    )
                    return
                if path == "/api/onboarding/confirm-fact":
                    self._send_json(app.confirm_candidate_fact(int(body.get("fact_id"))))
                    return
                if path == "/api/onboarding/reject-fact":
                    fact = app.storage.update_candidate_fact_status(int(body.get("fact_id")), "rejected")
                    if fact is None:
                        self._send_json({"error": "not_found"}, HTTPStatus.NOT_FOUND)
                        return
                    app.record_replay_event(
                        source="candidate",
                        event_type="candidate_fact_rejected",
                        title="Candidate fact rejected",
                        data={"fact": fact},
                    )
                    self._send_json(fact)
                    return
                if path == "/api/onboarding/generate-profile":
                    self._send_json(
                        {
                            **app.candidate_completeness(),
                            "profile": app.active_profile_info(),
                            "facts": app.candidate_facts(),
                        }
                    )
                    return
                if path == "/api/candidate/confirm-fact":
                    self._send_json(app.confirm_candidate_fact(int(body.get("fact_id"))))
                    return
                if path == "/api/candidate/profile":
                    self._send_json(app.update_profile(body))
                    return
                if path == "/api/candidate/facts":
                    self._send_json(_candidate_fact_payload(app, body))
                    return
                if path == "/api/resume-variants/build":
                    self._send_json(
                        app.build_resume_variant(
                            int(body.get("job_id")),
                            int(body.get("resume_id")),
                        )
                    )
                    return
                if path == "/api/resumes/build-variant":
                    self._send_json(
                        app.build_resume_variant(
                            int(body.get("job_id")),
                            int(body.get("resume_id")),
                        )
                    )
                    return
                if path == "/api/applications/build-pack":
                    self._send_json(
                        app.build_application_pack(
                            int(body.get("job_id")),
                            resume_variant=dict(body.get("resume_variant") or {}),
                            cover_letter=str(body.get("cover_letter") or ""),
                            short_message=str(body.get("short_message") or ""),
                            source_payload=dict(body.get("source_payload") or {}),
                            campaign_policy=dict(body.get("campaign_policy") or {}),
                        )
                    )
                    return
                if path.startswith("/api/applications/") and path.endswith("/preview"):
                    pack_id = _path_int(path.removesuffix("/preview"), "/api/applications/")
                    self._send_json(_application_pack_preview_payload(app, pack_id))
                    return
                if path.startswith("/api/applications/") and path.endswith("/dry-run"):
                    pack_id = _path_int(path.removesuffix("/dry-run"), "/api/applications/")
                    self._send_json(_application_pack_dry_run_payload(app, pack_id))
                    return
                if path.startswith("/api/applications/") and path.endswith("/apply"):
                    pack_id = _path_int(path.removesuffix("/apply"), "/api/applications/")
                    self._send_json(_application_pack_apply_payload(app, pack_id, body))
                    return
                if path == "/api/browser/setup":
                    self._send_json(app.browser_lab_setup(str(body.get("source") or "getmatch")))
                    return
                if path == "/api/browser/open-login":
                    self._send_json(
                        app.browser_lab_open_login(
                            str(body.get("source") or "getmatch"),
                            login_url=str(body.get("login_url") or ""),
                        )
                    )
                    return
                if path == "/api/browser/check-session":
                    self._send_json(app.browser_lab_status(str(body.get("source") or "getmatch")))
                    return
                if path == "/api/browser/record-flow":
                    source = str(body.get("source") or "getmatch")
                    status = app.browser_lab_status(source)
                    self._send_json(
                        {
                            "status": "planned",
                            "source": status["source"],
                            "submit": False,
                            "profile_dir": status["profile_dir"],
                            "screenshots_dir": status["screenshots_dir"],
                            "next_steps": [
                                "Open the source in the Browser Session Lab profile.",
                                "Record the apply flow as HAR or screenshots.",
                                "Import the redacted artifacts before enabling apply mapping.",
                            ],
                        }
                    )
                    return
                if path == "/api/browser/import-har":
                    hosts = {
                        str(item).strip().lower()
                        for item in (body.get("allowed_hosts") or [])
                        if str(item).strip()
                    }
                    self._send_json(
                        app.browser_lab_import_har(
                            str(body.get("source") or ""),
                            str(body.get("path") or ""),
                            allowed_hosts=hosts or None,
                        )
                    )
                    return
                if path == "/api/browser/map-form":
                    self._send_json(
                        app.browser_lab_map_form(
                            dict(body.get("form") or {}),
                            source=str(body.get("source") or ""),
                            persona=dict(body.get("persona") or {}),
                            resume=dict(body.get("resume") or {}),
                            vacancy=dict(body.get("vacancy") or {}),
                            extra_answers=dict(body.get("extra_answers") or {}),
                        )
                    )
                    return
                if path == "/api/browser/dry-run":
                    self._send_json(
                        app.browser_lab_dry_run_form_fill(
                            dict(body.get("form") or {}),
                            source=str(body.get("source") or ""),
                            persona=dict(body.get("persona") or {}),
                            resume=dict(body.get("resume") or {}),
                            vacancy=dict(body.get("vacancy") or {}),
                            extra_answers=dict(body.get("extra_answers") or {}),
                        )
                    )
                    return
                if path == "/api/browser/execute-dry-run":
                    self._send_json(
                        app.browser_lab_execute_dry_run_form_fill(
                            dict(body.get("form") or {}),
                            source=str(body.get("source") or ""),
                            persona=dict(body.get("persona") or {}),
                            resume=dict(body.get("resume") or {}),
                            vacancy=dict(body.get("vacancy") or {}),
                            extra_answers=dict(body.get("extra_answers") or {}),
                            headless=bool(body.get("headless", True)),
                            timeout_ms=int(body.get("timeout_ms") or 15000),
                        )
                    )
                    return
                if path == "/api/browser-lab/open-login":
                    self._send_json(
                        app.browser_lab_open_login(
                            str(body.get("source") or "getmatch"),
                            login_url=str(body.get("login_url") or ""),
                        )
                    )
                    return
                if path == "/api/browser-lab/import-har":
                    hosts = {
                        str(item).strip().lower()
                        for item in (body.get("allowed_hosts") or [])
                        if str(item).strip()
                    }
                    if bool(body.get("configure_external_apply", False)):
                        self._send_json(
                            app.configure_source_external_apply_from_har(
                                str(body.get("source") or ""),
                                str(body.get("path") or ""),
                                allowed_hosts=hosts or None,
                                level=int(body.get("level") or 5),
                            )
                        )
                        return
                    self._send_json(
                        app.browser_lab_import_har(
                            str(body.get("source") or ""),
                            str(body.get("path") or ""),
                            allowed_hosts=hosts or None,
                        )
                    )
                    return
                if path == "/api/browser-lab/forms/map":
                    self._send_json(
                        app.browser_lab_map_form(
                            dict(body.get("form") or {}),
                            source=str(body.get("source") or ""),
                            persona=dict(body.get("persona") or {}),
                            resume=dict(body.get("resume") or {}),
                            vacancy=dict(body.get("vacancy") or {}),
                            extra_answers=dict(body.get("extra_answers") or {}),
                        )
                    )
                    return
                if path == "/api/browser-lab/forms/dry-run":
                    self._send_json(
                        app.browser_lab_dry_run_form_fill(
                            dict(body.get("form") or {}),
                            source=str(body.get("source") or ""),
                            persona=dict(body.get("persona") or {}),
                            resume=dict(body.get("resume") or {}),
                            vacancy=dict(body.get("vacancy") or {}),
                            extra_answers=dict(body.get("extra_answers") or {}),
                        )
                    )
                    return
                if path == "/api/browser-lab/forms/execute-dry-run":
                    self._send_json(
                        app.browser_lab_execute_dry_run_form_fill(
                            dict(body.get("form") or {}),
                            source=str(body.get("source") or ""),
                            persona=dict(body.get("persona") or {}),
                            resume=dict(body.get("resume") or {}),
                            vacancy=dict(body.get("vacancy") or {}),
                            extra_answers=dict(body.get("extra_answers") or {}),
                            headless=bool(body.get("headless", True)),
                            timeout_ms=int(body.get("timeout_ms") or 15000),
                        )
                    )
                    return
                if path == "/api/hh/search-campaigns/plan":
                    self._send_json(
                        app.plan_hh_search_campaign(
                            text=body.get("text"),
                            area=body.get("area"),
                            professional_role=body.get("professional_role"),
                            industry=body.get("industry"),
                            salary=body.get("salary"),
                            schedule=body.get("schedule"),
                            experience=body.get("experience"),
                            employment=body.get("employment"),
                            date_from=body.get("date_from"),
                            date_to=body.get("date_to"),
                            search_field=body.get("search_field"),
                            employer_id=body.get("employer_id"),
                            excluded_employer_id=body.get("excluded_employer_id"),
                            only_with_salary=bool(body.get("only_with_salary", False)),
                            limit=int(body.get("limit", 100)),
                            page=int(body.get("page", 0)),
                            min_score=int(body.get("min_score", 0)),
                            skip_tests=bool(body.get("skip_tests", False)),
                            ai_filter_mode=str(body.get("ai_filter_mode") or "off"),
                            resume_id=body.get("resume_id"),
                            daily_cap=_optional_body_int(body, "daily_cap"),
                        )
                    )
                    return
                if path == "/api/hh/resumes/sync":
                    self._send_json(app.sync_hh_resumes())
                    return
                if path == "/api/hh/token/refresh":
                    self._send_json(mask_secrets(app.refresh_hh_token()))
                    return
                if path == "/api/hh/resumes/update":
                    self._send_json(app.update_hh_resumes())
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
                if path == "/api/hh/skipped/clear":
                    self._send_json(app.clear_hh_skipped_vacancies())
                    return
                if path == "/api/hh/call":
                    self._send_json(
                        app.hh_call_api(
                            str(body.get("method") or "GET"),
                            str(body.get("path") or "/"),
                            data=body.get("data"),
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
                            limit=None if body.get("limit") is None else int(body.get("limit")),
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
                if path == "/api/campaigns/presets":
                    self._send_json(
                        app.save_hh_campaign_preset(
                            str(body.get("name") or ""),
                            dict(body.get("params") or {}),
                        )
                    )
                    return
                if path == "/api/campaigns/plan":
                    self._send_json(app.plan_hh_campaign(**_campaign_plan_kwargs(app, body)))
                    return
                if path == "/api/campaigns/external/plan":
                    self._send_json(
                        app.plan_external_campaign(
                            str(body.get("source") or ""),
                            limit=int(body.get("limit", 100)),
                            min_score=int(body.get("min_score", 0)),
                            daily_cap=_optional_body_int(body, "daily_cap"),
                        )
                    )
                    return
                if path.startswith("/api/campaigns/") and path.endswith("/run-external"):
                    run_id = _path_int(path.removesuffix("/run-external"), "/api/campaigns/")
                    self._send_json(app.confirm_external_campaign(run_id, confirm=bool(body.get("real") or body.get("confirm"))))
                    return
                if path.startswith("/api/campaigns/") and path.endswith("/review"):
                    run_id = _path_int(path.removesuffix("/review"), "/api/campaigns/")
                    self._send_json(_campaign_review_payload(app, run_id))
                    return
                if path.startswith("/api/campaigns/") and path.endswith("/enable"):
                    run_id = _path_int(path.removesuffix("/enable"), "/api/campaigns/")
                    app.enable_hh_campaign(run_id)
                    self._send_json(_campaign_review_payload(app, run_id))
                    return
                if path.startswith("/api/campaigns/") and path.endswith("/run"):
                    run_id = _path_int(path.removesuffix("/run"), "/api/campaigns/")
                    self._send_json(
                        app.confirm_enabled_hh_campaign(
                            run_id,
                            confirm=bool(body.get("real") or body.get("confirm")),
                        )
                    )
                    return
                if path.startswith("/api/campaigns/") and path.endswith("/pause"):
                    run_id = _path_int(path.removesuffix("/pause"), "/api/campaigns/")
                    self._send_json(app.pause_hh_agent(reason=str(body.get("reason") or f"campaign:{run_id}")))
                    return
                if path.startswith("/api/campaigns/") and path.endswith("/resume"):
                    run_id = _path_int(path.removesuffix("/resume"), "/api/campaigns/")
                    result = app.resume_hh_agent(reason=str(body.get("reason") or f"campaign:{run_id}"))
                    self._send_json({**result, "id": run_id})
                    return
                if path.startswith("/api/campaigns/") and path.endswith("/kill"):
                    run_id = _path_int(path.removesuffix("/kill"), "/api/campaigns/")
                    result = app.pause_hh_agent(reason=str(body.get("reason") or f"campaign_kill:{run_id}"))
                    run = app.storage.get_hh_campaign_run(run_id)
                    app.storage.update_hh_campaign_run(
                        run_id,
                        status="killed",
                        counts=dict(run.counts if run else {}),
                        finished=True,
                    )
                    self._send_json({**result, "status": "killed", "id": run_id})
                    return
                if path == "/api/hh/campaigns/plan":
                    self._send_json(
                        app.plan_hh_campaign(
                            limit=int(body.get("limit", 100)),
                            min_score=int(body.get("min_score", 0)),
                            skip_tests=bool(body.get("skip_tests", False)),
                            ai_filter_mode=str(body.get("ai_filter_mode") or "off"),
                            resume_id=body.get("resume_id"),
                            daily_cap=_optional_body_int(body, "daily_cap"),
                        )
                    )
                    return
                if path.startswith("/api/hh/campaigns/") and path.endswith("/confirm"):
                    run_id = _path_int(path.removesuffix("/confirm"), "/api/hh/campaigns/")
                    self._send_json(
                        app.confirm_hh_campaign(
                            run_id,
                            confirm=bool(body.get("confirm")),
                        )
                    )
                    return
                if path == "/api/chat":
                    messages = body.get("messages", [])
                    job_id = body.get("job_id")
                    result = app.chat(messages, job_id=job_id)
                    self._send_json({"content": result})
                    return
                if path == "/api/config":
                    updated = _deep_merge(app.config, body)
                    app.save_config(updated)
                    self._send_json(mask_secrets(updated))
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
                if path.startswith("/api/jobs/") and path.endswith("/letter-preview"):
                    job_id = _path_int(path.removesuffix("/letter-preview"), "/api/jobs/")
                    self._send_json(
                        app.cover_letter_preview(
                            job_id,
                            template=str(body.get("template") or "A"),
                            use_for_campaign=bool(body.get("use_for_campaign")),
                        )
                    )
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
                if path.startswith("/api/jobs/") and path.endswith("/external-apply/dry-run"):
                    job_id = _path_int(path.removesuffix("/external-apply/dry-run"), "/api/jobs/")
                    self._send_json(
                        app.external_apply_dry_run(
                            job_id,
                            form=dict(body.get("form") or {}),
                            resume_variant=dict(body.get("resume_variant") or {}),
                            cover_letter=str(body.get("cover_letter") or ""),
                            short_message=str(body.get("short_message") or ""),
                            campaign_policy=dict(body.get("campaign_policy") or {}),
                            extra_answers=dict(body.get("extra_answers") or {}),
                        )
                    )
                    return
                if path.startswith("/api/jobs/") and path.endswith("/external-apply/confirm"):
                    job_id = _path_int(path.removesuffix("/external-apply/confirm"), "/api/jobs/")
                    self._send_json(
                        app.confirm_external_apply(
                            job_id,
                            form=dict(body.get("form") or {}),
                            resume_variant=dict(body.get("resume_variant") or {}),
                            cover_letter=str(body.get("cover_letter") or ""),
                            short_message=str(body.get("short_message") or ""),
                            campaign_policy=dict(body.get("campaign_policy") or {}),
                            extra_answers=dict(body.get("extra_answers") or {}),
                            confirm=bool(body.get("confirm")),
                            submit=bool(body.get("submit_certified") or body.get("submit")),
                            campaign_policy_apply=bool(body.get("campaign_policy_apply")),
                        )
                    )
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
                            confirm=bool(body.get("confirm")),
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
                if path == "/api/resumes/import":
                    self._send_json(
                        app.import_resume(
                            str(body.get("path") or ""),
                            activate=bool(body.get("activate", False)),
                        )
                    )
                    return
                if path == "/api/resumes/parse":
                    self._send_json(import_resume_file(str(body.get("path") or "")))
                    return
                if path == "/api/resumes":
                    resume = Resume(
                        name=body.get("name", ""),
                        body=body.get("body", ""),
                        profile_id=body.get("profile_id", "default"),
                        is_active=body.get("is_active", False),
                        canonical=dict(body.get("canonical") or {}),
                        source_format=str(body.get("source_format") or ""),
                        imported_from=str(body.get("imported_from") or ""),
                    )
                    if body.get("id"):
                        resume.id = body["id"]
                    saved_id = app.storage.save_resume(resume)
                    self._send_json({"id": saved_id})
                    return
                if path == "/api/resumes/ats-score":
                    resume_text = body.get("resume_text", "")
                    result = app.ats_score_resume(resume_text)
                    self._send_json(result)
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
                if path.startswith("/api/pipeline/jobs/") and path.endswith("/prep-pack"):
                    job_id = _path_int(path.removesuffix("/prep-pack"), "/api/pipeline/jobs/")
                    self._send_json(
                        app.interview_prep_pack(
                            job_id,
                            stage=str(body.get("stage") or "tech"),
                        )
                    )
                    return
                if path.startswith("/api/pipeline/jobs/") and path.endswith("/event"):
                    job_id = _path_int(path.removesuffix("/event"), "/api/pipeline/jobs/")
                    self._send_json(
                        app.schedule_pipeline_event(
                            job_id,
                            event_type=str(body.get("event_type") or "follow_up"),
                            event_at=str(body.get("event_at") or body.get("event_date") or ""),
                            title=str(body.get("title") or ""),
                            notes=str(body.get("notes") or ""),
                        )
                    )
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
                    result = app.classify_jobs_batch(job_ids)
                    self._send_json(result)
                    return
                if path == "/api/market-trends":
                    limit = body.get("limit", 50)
                    result = app.market_trends(limit)
                    self._send_json({"content": result})
                    return
                if path == "/api/behavior/suggest":
                    result = app.behavior_suggest()
                    self._send_json({"content": result})
                    return
                if path.startswith("/api/jobs/") and path.endswith("/parse-structure"):
                    job_id = _path_int(path.removesuffix("/parse-structure"), "/api/jobs/")
                    result = app.parse_job_structure(job_id)
                    self._send_json(result)
                    return
                if path.startswith("/api/jobs/") and path.endswith("/gap-analysis"):
                    job_id = _path_int(path.removesuffix("/gap-analysis"), "/api/jobs/")
                    resume_id = body.get("resume_id", 0)
                    result = app.gap_analysis(resume_id, job_id)
                    self._send_json({"content": result})
                    return
                if path.startswith("/api/jobs/") and path.endswith("/smart-classify"):
                    job_id = _path_int(path.removesuffix("/smart-classify"), "/api/jobs/")
                    result = app.smart_classify(job_id)
                    self._send_json(result)
                    return
                if path.startswith("/api/jobs/") and path.endswith("/interview-prep"):
                    job_id = _path_int(path.removesuffix("/interview-prep"), "/api/jobs/")
                    stage = body.get("stage", "tech")
                    result = app.interview_stage_prep(job_id, stage)
                    self._send_json({"content": result})
                    return
                if path.startswith("/api/jobs/") and path.endswith("/record-event"):
                    job_id = _path_int(path.removesuffix("/record-event"), "/api/jobs/")
                    action = body.get("action", "viewed")
                    app.storage.record_event(job_id, action)
                    self._send_json({"status": "ok"})
                    return
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
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


def _campaign_review_payload(app: WorkHunter, run_id: int) -> dict[str, Any]:
    run = app.storage.get_hh_campaign_run(run_id)
    items = []
    for item in app.storage.list_hh_campaign_items(run_id):
        payload = item.to_dict()
        job = app.storage.get_job(item.job_id)
        payload["job"] = job.to_dict() if job else None
        items.append(payload)
    return {"run": run.to_dict() if run else None, "items": items}


def _campaign_plan_kwargs(app: WorkHunter, body: dict[str, Any]) -> dict[str, Any]:
    params: dict[str, Any] = {}
    preset = str(body.get("preset") or "").strip()
    if preset:
        params.update(app.get_hh_campaign_preset(preset)["params"])
    for key in ["limit", "min_score", "skip_tests", "ai_filter_mode", "resume_id", "daily_cap"]:
        if body.get(key) is not None:
            params[key] = body[key]
    return {
        "limit": int(params.get("limit") or 100),
        "min_score": int(params.get("min_score") or 0),
        "skip_tests": bool(params.get("skip_tests", False)),
        "ai_filter_mode": str(params.get("ai_filter_mode") or "off"),
        "resume_id": params.get("resume_id") or None,
        "daily_cap": _optional_body_int(params, "daily_cap"),
    }


def _source_sync_payload(app: WorkHunter, source: str, body: dict[str, Any]) -> dict[str, Any]:
    source_name = source.strip().lower().replace("-", "_")
    limit = _optional_body_int(body, "limit")
    if limit == 0:
        return {source_name: {"status": "planned", "count": 0, "dry_run": True}}
    result = app.sync_sources(sources=[source_name], limit=limit)
    if body.get("score", False):
        result["scored"] = app.score_jobs()
    return result


def _redaction_scan_payload(body: dict[str, Any]) -> dict[str, Any]:
    text = str(body.get("text") or "")
    redacted_text = redact_secrets(text)
    payload = mask_secrets({key: value for key, value in body.items() if key != "text"})
    return {
        "status": "ok",
        "redacted": {
            **payload,
            "text": redacted_text,
        },
        "findings": {
            "changed": redacted_text != text or payload != {key: value for key, value in body.items() if key != "text"},
        },
    }


def _promote_source_maturity_payload(app: WorkHunter, source: str, body: dict[str, Any]) -> dict[str, Any]:
    source_name = source.strip().lower().replace("-", "_")
    level = int(body.get("level") or body.get("maturity_level") or 0)
    external_apply = ((app.config.get("sources") or {}).get(source_name) or {}).get("external_apply") or {}
    payload_template = body.get("payload_template")
    if not isinstance(payload_template, dict):
        payload_template = external_apply.get("payload_template") if isinstance(external_apply, dict) else {}
    app.configure_source_external_apply_target(
        source_name,
        session=str(body.get("session") or (external_apply.get("session") if isinstance(external_apply, dict) else "") or source_name),
        url=str(body.get("url") or (external_apply.get("url") if isinstance(external_apply, dict) else "") or ""),
        method=str(body.get("method") or (external_apply.get("method") if isinstance(external_apply, dict) else "") or "POST"),
        payload_template=payload_template if isinstance(payload_template, dict) else {},
        level=level,
    )
    evidence = body.get("evidence") or body.get("certification")
    if not isinstance(evidence, dict):
        evidence = {
            key: body[key]
            for key in ("tests", "replay", "redaction", "dry_run")
            if key in body and isinstance(body[key], dict)
        }
    promotion = app.promote_source_certification(source_name, level=level, evidence=evidence if isinstance(evidence, dict) else {})
    report = app.source_capabilities()
    return {source_name: {**(report.get(source_name) or {}), "promotion": promotion}}


def _candidate_fact_payload(app: WorkHunter, body: dict[str, Any]) -> dict[str, Any]:
    key = str(body.get("key") or body.get("question_id") or "fact")
    value = body.get("value", body.get("answer", ""))
    question_ids = {question["id"] for question in app.onboarding_questions()}
    if key in question_ids and body.get("answer") is not None:
        return app.answer_onboarding(
            key,
            str(body.get("answer") or ""),
            source=str(body.get("source") or "web"),
        )
    profile_id = str(app.config.get("profile") or "default")
    fact_id = app.storage.save_candidate_fact(
        profile_id=profile_id,
        category=str(body.get("category") or key),
        key=key,
        value=value,
        confidence=float(body.get("confidence") or 0.7),
        source=str(body.get("source") or "web"),
        status=str(body.get("status") or "unconfirmed"),
        evidence=dict(body.get("evidence") or {"tool": "candidate_facts_api"}),
    )
    fact = app.storage.list_candidate_facts(profile_id=profile_id)[-1]
    app.record_replay_event(
        source="candidate",
        event_type="candidate_fact_created",
        title="Candidate fact captured",
        data={"fact": fact},
    )
    return {"status": "recorded", "facts": [{"id": fact_id, **fact}]}


def _application_pack_preview_payload(app: WorkHunter, pack_id: int) -> dict[str, Any]:
    pack = app.storage.get_application_pack(pack_id)
    if pack is None:
        raise ValueError(f"Application pack {pack_id} not found")
    return {
        "status": "preview",
        "id": pack_id,
        "job_id": pack["job_id"],
        "submit": False,
        "application_pack": pack,
        "preview": pack.get("preview") or {},
    }


def _application_pack_dry_run_payload(app: WorkHunter, pack_id: int) -> dict[str, Any]:
    pack = app.storage.get_application_pack(pack_id)
    if pack is None:
        raise ValueError(f"Application pack {pack_id} not found")
    status = "dry_run_ready" if pack.get("policy_status") == "ready" else "blocked_manual_review"
    return {
        "status": status,
        "id": pack_id,
        "job_id": pack["job_id"],
        "submit": False,
        "policy_status": pack.get("policy_status"),
        "policy_reasons": pack.get("policy_reasons") or [],
        "preview": pack.get("preview") or {},
    }


def _application_pack_apply_payload(app: WorkHunter, pack_id: int, body: dict[str, Any]) -> dict[str, Any]:
    pack = app.storage.get_application_pack(pack_id)
    if pack is None:
        raise ValueError(f"Application pack {pack_id} not found")
    if not bool(body.get("confirm")):
        return {
            "status": "blocked",
            "reason": "confirmation_required",
            "id": pack_id,
            "job_id": pack["job_id"],
            "submit": False,
        }
    return {
        "status": "blocked",
        "reason": "real_apply_requires_campaign_policy",
        "id": pack_id,
        "job_id": pack["job_id"],
        "submit": False,
    }


def _replay_screenshot_path(root: Path, app: WorkHunter, event: dict[str, Any] | None, name: str) -> Path | None:
    if event is None:
        return None
    stored = app.storage.get_replay_screenshot(int(event["id"]), name)
    stored_path = _safe_replay_file_path(root, stored.get("path") if stored else "")
    if stored_path is not None:
        return stored_path
    screenshots = (event.get("data") or {}).get("screenshots") or {}
    if not isinstance(screenshots, dict):
        return None
    raw_path = screenshots.get(name) or screenshots.get("path")
    return _safe_replay_file_path(root, raw_path)


def _safe_replay_file_path(root: Path, raw_path: Any) -> Path | None:
    if not raw_path:
        return None
    path = Path(str(raw_path)).resolve()
    root_path = Path(root).resolve()
    try:
        path.relative_to(root_path)
    except ValueError:
        return None
    if not path.exists() or not path.is_file():
        return None
    return path


def _path_int(path: str, prefix: str) -> int:
    return int(path.removeprefix(prefix).strip("/"))


def _int_arg(query: dict[str, list[str]], name: str, default: int) -> int:
    value = query.get(name, [str(default)])[0]
    return int(value)


def _optional_int_arg(query: dict[str, list[str]], name: str) -> int | None:
    value = query.get(name, [None])[0]
    return int(value) if value not in (None, "") else None


def _optional_body_int(body: dict[str, Any], name: str) -> int | None:
    value = body.get(name)
    return int(value) if value not in (None, "") else None


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


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


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
    applied = by_status.get("applied", 0)
    funnel: dict[str, int] = {
        "new": by_status.get("new", 0),
        "saved": by_status.get("saved", 0),
        "applied": by_status.get("applied", 0),
        "phone_screen": by_status.get("phone_screen", 0),
        "interview": by_status.get("interview", 0),
        "offer": by_status.get("offer", 0),
        "rejected": by_status.get("rejected", 0),
        "not_interested": by_status.get("not_interested", 0),
    }
    return {
        "total_jobs": total,
        "by_source": by_source,
        "by_status": by_status,
        "score_distribution": score_buckets,
        "total_applications": applied,
        "applications_by_status": funnel,
    }
