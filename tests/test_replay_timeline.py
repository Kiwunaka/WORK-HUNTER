from __future__ import annotations

import json
import threading
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer

from work_hunter.replay.events import TIMELINE_EVENT_TYPES, normalize_timeline_event
from work_hunter.replay.renderer import render_timeline_cards
from work_hunter.replay.timeline import build_timeline
from work_hunter.services import WorkHunter
from work_hunter.web.server import make_handler


def test_replay_filters_events_and_hashes_prompt_output(tmp_path):
    app = WorkHunter(root=tmp_path)
    app.record_replay_event(
        job_id=1,
        source="hh",
        event_type="ai_generation",
        title="Letter generated",
        data={"prompt": "secret prompt", "output": "letter text"},
    )
    app.record_replay_event(
        job_id=1,
        source="hh",
        event_type="policy_decision",
        title="Policy pass",
        data={"result": "ready"},
    )

    replay = app.replay_for_job(1, event_type="ai_generation")

    assert [event["event_type"] for event in replay["events"]] == ["ai_generation"]
    assert replay["events"][0]["data"]["prompt_hash"]
    assert replay["events"][0]["data"]["output_hash"]
    assert "prompt" not in replay["events"][0]["data"]
    assert "output" not in replay["events"][0]["data"]


def test_replay_redacts_sensitive_strings_and_drops_raw_prompt_output(tmp_path):
    app = WorkHunter(root=tmp_path)
    app.record_replay_event(
        job_id=4,
        source="hh",
        event_type="ai_generation",
        title="Authorization: Bearer title-secret",
        summary="Cookie: sid=summary-secret",
        data={
            "prompt": "client_secret=prompt-secret",
            "output": "access_token=output-secret",
            "note": "refresh_token=note-secret",
            "nested": {"ordinary": "Authorization: Bearer nested-secret"},
        },
    )

    event = app.replay_for_job(4)["events"][0]
    serialized = json.dumps(event, ensure_ascii=False)

    assert "title-secret" not in serialized
    assert "summary-secret" not in serialized
    assert "prompt-secret" not in serialized
    assert "output-secret" not in serialized
    assert "note-secret" not in serialized
    assert "nested-secret" not in serialized
    assert event["data"]["prompt_hash"].startswith("sha256:")
    assert event["data"]["output_hash"].startswith("sha256:")
    assert "prompt" not in event["data"]
    assert "output" not in event["data"]


def test_replay_package_normalizes_timeline_schema_and_renders_cards():
    event = normalize_timeline_event(
        {
            "id": 10001,
            "run_id": 7,
            "job_id": 123,
            "source": "getmatch",
            "created_at": "2026-06-13T12:00:00+02:00",
            "event_type": "cover_letter_generated",
            "title": "Сгенерировано сопроводительное",
            "summary": "Template: direct_human, 112 words",
            "data": {"template": "direct_human", "Authorization": "***"},
        },
        actor="campaign-runner",
    )
    timeline = build_timeline([event])
    cards = render_timeline_cards(timeline)

    assert "campaign_started" in TIMELINE_EVENT_TYPES
    assert "manual_review_required" in TIMELINE_EVENT_TYPES
    assert event["timestamp"] == "2026-06-13T12:00:00+02:00"
    assert event["actor"] == "campaign-runner"
    assert event["redacted"] is True
    assert timeline["events"][0]["id"] == 10001
    assert cards == ["12:00 Сгенерировано сопроводительное — Template: direct_human, 112 words"]


def test_replay_markdown_export_reconstructs_application_story(tmp_path):
    app = WorkHunter(root=tmp_path)
    app.record_replay_event(job_id=2, source="hh", event_type="selected", title="Selected", summary="score 95")
    app.record_replay_event(
        job_id=2,
        source="hh",
        event_type="application_pack_built",
        title="Payload preview",
        summary="ready",
        data={"sent": {"resume": "r1", "letter": "Hi"}},
    )

    markdown = app.export_replay_markdown(job_id=2)

    assert "# Replay Timeline" in markdown
    assert "why selected" in markdown
    assert "what generated/sent" in markdown
    assert "Selected" in markdown
    assert "Payload preview" in markdown


def test_replay_web_api_supports_filter_and_markdown_export(tmp_path):
    app = WorkHunter(root=tmp_path)
    app.record_replay_event(job_id=3, source="hh", event_type="selected", title="Selected")
    app.record_replay_event(job_id=3, source="geekjob", event_type="selected", title="Other source")

    with _server(tmp_path) as base:
        filtered = _get_json(base, "/api/replay/jobs/3?source=hh")
        markdown = _get_text(base, "/api/replay/jobs/3/export?source=hh")

    assert [event["source"] for event in filtered["events"]] == ["hh"]
    assert "Other source" not in markdown
    assert "Selected" in markdown


def test_replay_screenshot_endpoint_uses_screenshot_store(tmp_path):
    app = WorkHunter(root=tmp_path)
    screenshot = tmp_path / ".work-hunter" / "browser_screenshots" / "hirehi" / "before.png"
    screenshot.parent.mkdir(parents=True, exist_ok=True)
    screenshot.write_bytes(b"\x89PNG\r\n\x1a\nstore")
    event_id = app.record_replay_event(
        job_id=5,
        source="hirehi",
        event_type="browser_screenshot",
        title="Before submit",
    )
    app.storage.add_replay_screenshot(event_id, "before", screenshot)

    with _server(tmp_path) as base:
        body, content_type = _get_bytes(base, f"/api/replay/events/{event_id}/screenshot?name=before")

    assert body == b"\x89PNG\r\n\x1a\nstore"
    assert content_type == "image/png"


class _server:
    def __init__(self, root):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(root))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return f"http://127.0.0.1:{self.server.server_port}"

    def __exit__(self, exc_type, exc, tb):
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()


def _get_json(base: str, path: str):
    with urllib.request.urlopen(f"{base}{path}", timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def _get_text(base: str, path: str):
    safe_path = urllib.parse.quote(path, safe="/:?=&")
    with urllib.request.urlopen(f"{base}{safe_path}", timeout=5) as response:
        return response.read().decode("utf-8")


def _get_bytes(base: str, path: str):
    with urllib.request.urlopen(f"{base}{path}", timeout=5) as response:
        return response.read(), response.headers.get("Content-Type")
