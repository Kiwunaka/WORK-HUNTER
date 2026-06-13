from __future__ import annotations

import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

from work_hunter.hh_agent.events import detect_hh_agent_items
from work_hunter.services import WorkHunter
from work_hunter.web.server import make_handler


def _event_messages() -> list[dict]:
    return [
        {
            "id": "m-interview",
            "negotiation_id": "neg-1",
            "author": {"participant_type": "employer"},
            "text": "Interview on 2026-06-12 15:30 with the backend lead.",
            "created_at": "2026-06-10T10:00:00",
            "vacancy": {"id": "vac-1", "name": "Python Developer"},
            "employer": {"id": "emp-1", "name": "Acme"},
        },
        {
            "id": "m-test",
            "negotiation_id": "neg-1",
            "author": {"participant_type": "employer"},
            "text": "Please complete the test task by 2026-06-13 18:00.",
            "created_at": "2026-06-10T11:00:00",
            "vacancy": {"id": "vac-1", "name": "Python Developer"},
            "employer": {"id": "emp-1", "name": "Acme"},
        },
        {
            "id": "m-rejection",
            "negotiation_id": "neg-2",
            "author": {"participant_type": "employer"},
            "text": "Unfortunately we cannot proceed with your candidacy.",
            "created_at": "2026-06-10T12:00:00",
            "vacancy": {"id": "vac-2", "name": "Go Developer"},
            "employer": {"id": "emp-2", "name": "Beta"},
        },
        {
            "id": "m-offer",
            "negotiation_id": "neg-3",
            "author": {"participant_type": "employer"},
            "text": "We are ready to make an offer. Let's discuss details tomorrow.",
            "created_at": "2026-06-10T13:00:00",
            "vacancy": {"id": "vac-3", "name": "Platform Engineer"},
            "employer": {"id": "emp-3", "name": "Delta"},
        },
        {
            "id": "m-applicant",
            "negotiation_id": "neg-4",
            "author": {"participant_type": "applicant"},
            "text": "I can join the interview on 2026-06-15 10:00.",
            "created_at": "2026-06-10T14:00:00",
        },
    ]


def _get_json(base: str, path: str):
    with urllib.request.urlopen(f"{base}{path}", timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def _get_text(base: str, path: str) -> str:
    with urllib.request.urlopen(f"{base}{path}", timeout=5) as response:
        return response.read().decode("utf-8")


def _post_json(base: str, path: str, payload: dict):
    request = urllib.request.Request(
        f"{base}{path}",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def test_detect_hh_agent_items_classifies_events_tasks_and_ignores_applicant_messages():
    detected = detect_hh_agent_items(_event_messages(), now="2026-06-10T09:00:00")

    assert [event.event_type for event in detected.events] == ["interview", "rejection", "offer"]
    assert [task.task_type for task in detected.tasks] == ["test"]
    assert detected.events[0].event_at == "2026-06-12T15:30:00"
    assert detected.tasks[0].due_at == "2026-06-13T18:00:00"
    assert detected.events[0].payload["vacancy_name"] == "Python Developer"
    assert "m-applicant" not in {event.source_id for event in detected.events}


def test_scan_hh_agent_events_persists_items_and_adds_digest_agenda(tmp_path):
    app = WorkHunter(root=tmp_path)

    result = app.scan_hh_agent_events(messages=_event_messages(), now="2026-06-10T09:00:00")
    repeated = app.scan_hh_agent_events(messages=_event_messages(), now="2026-06-10T09:00:00")
    digest = app.hh_agent_digest(limit=5)

    assert result["status"] == "ok"
    assert result["counts"] == {"events": 3, "tasks": 1}
    assert repeated["counts"] == {"events": 3, "tasks": 1}
    assert len(app.storage.list_hh_agent_events()) == 3
    assert len(app.storage.list_hh_agent_tasks()) == 1
    assert digest["agenda"]["events"]["total"] == 3
    assert digest["agenda"]["tasks"]["total"] == 1


def test_hh_agent_calendar_and_markdown_exports(tmp_path):
    app = WorkHunter(root=tmp_path)
    app.scan_hh_agent_events(messages=_event_messages(), now="2026-06-10T09:00:00")

    ics = app.export_hh_agent_calendar_ics()
    markdown = app.export_hh_agent_agenda_markdown()

    assert "BEGIN:VCALENDAR" in ics
    assert "BEGIN:VEVENT" in ics
    assert "BEGIN:VTODO" in ics
    assert "SUMMARY:Interview: Python Developer" in ics
    assert "DUE:20260613T180000" in ics
    assert "## Events" in markdown
    assert "Interview: Python Developer" in markdown
    assert "Test: Python Developer" in markdown


def test_hh_agent_event_web_api_exposes_lists_scan_and_exports(tmp_path):
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(tmp_path))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        scan = _post_json(
            base,
            "/api/agent/events/scan",
            {"messages": _event_messages(), "now": "2026-06-10T09:00:00"},
        )
        events = _get_json(base, "/api/agent/events")
        tasks = _get_json(base, "/api/agent/tasks")
        agenda = _get_text(base, "/api/agent/agenda.md")
        calendar = _get_text(base, "/api/agent/calendar.ics")
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    assert scan["counts"] == {"events": 3, "tasks": 1}
    assert events[0]["event_type"] == "interview"
    assert tasks[0]["task_type"] == "test"
    assert "## Events" in agenda
    assert "BEGIN:VCALENDAR" in calendar
