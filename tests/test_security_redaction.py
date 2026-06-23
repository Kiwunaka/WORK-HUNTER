from __future__ import annotations

import json
from pathlib import Path

from work_hunter.security.path_safety import is_safe_project_path
from work_hunter.security.policy import safety_policy
from work_hunter.security.redaction import redact_secrets
from work_hunter.security.secret_scan import find_secret_markers
from work_hunter.services import WorkHunter


def test_security_package_redacts_scans_and_blocks_unsafe_paths(tmp_path):
    text = (
        "Authorization: Bearer token\n"
        "Cookie: sid=secret\n"
        "client_secret=abc\n"
        "OpenRouter key sk-or-v1-secret\n"
        "https://x.test/?access_token=raw"
    )
    redacted = redact_secrets(text)
    markers = find_secret_markers(text)

    assert "token" not in redacted
    assert "sid=secret" not in redacted
    assert "client_secret=abc" not in redacted
    assert "sk-or-v1-secret" not in redacted
    assert "access_token=raw" not in redacted
    assert {"Authorization", "Cookie", "client_secret", "access_token"} <= set(markers)
    assert is_safe_project_path(tmp_path, tmp_path / ".work-hunter" / "state.json") is True
    assert is_safe_project_path(tmp_path, Path.home() / ".codex" / "auth.json") is False
    assert safety_policy()["forbidden_outputs"] >= {"Authorization", "Cookie", "api_key", "browser localStorage raw"}


def test_audit_log_storage_redacts_sensitive_payloads_and_status_counts(tmp_path):
    app = WorkHunter(root=tmp_path)

    audit_id = app.record_audit_log(
        event_type="api_error",
        actor="test",
        data={
            "headers": {"Authorization": "Bearer audit-secret", "Cookie": "sid=audit-secret"},
            "url": "https://example.test/?access_token=audit-secret",
        },
    )
    logs = app.storage.list_audit_logs()
    status = app.security_status()
    serialized = json.dumps({"logs": logs, "status": status}, ensure_ascii=False)

    assert audit_id == logs[0]["id"]
    assert logs[0]["event_type"] == "api_error"
    assert logs[0]["data"]["headers"] == {"Authorization": "***", "Cookie": "***"}
    assert status["audit_logs"]["count"] == 1
    assert status["audit_logs"]["latest_event_type"] == "api_error"
    assert "audit-secret" not in serialized
