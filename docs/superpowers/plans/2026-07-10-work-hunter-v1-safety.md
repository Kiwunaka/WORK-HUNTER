# Work Hunter 1.0 Safety Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close every confirmed live-action, local HTTP, masked-config, and resume-mutation safety defect without making a real HH request.

**Architecture:** Add a small shared mutation guard, keep service methods as the final authorization boundary, and put loopback/Origin/Host/content-type validation in a focused web-security module. Preserve current CLI and HTTP shapes where safe, but require literal booleans and explicit real-mode flags for account mutations.

**Tech Stack:** Python 3.11+, pytest, standard-library `http.server`, vanilla JavaScript, SQLite audit tables.

## Global Constraints

- Never read or modify `.work-hunter` from the repository root; every test uses `tmp_path`.
- Real applications and other account mutations are supported through the shared plan/confirm pipeline. Every interactive mutation requires literal confirmation; explicitly enabled autopilot grants are the only unattended exception.
- Confirmation authorizes a mutation only when the Python value is exactly `True`.
- UI binds only to `127.0.0.1`, `localhost`, or `::1`.
- Responses and logs never contain access tokens, refresh tokens, client secrets, cookies, Telegram tokens, SMTP passwords, or API keys.
- Do not add blanket mypy ignores or weaken existing safety tests.

---

### Task 1: Shared Literal Confirmation And Guarded HH API Lab

**Files:**
- Create: `work_hunter/safety.py`
- Modify: `work_hunter/services.py` (`hh_call_api`, `hh_api_lab_call`)
- Modify: `work_hunter/web/server.py` (all confirm-bearing routes)
- Modify: `work_hunter/web/static/app.js` (`runHhLabRequest`)
- Test: `tests/test_hh_api_lab.py`
- Test: `tests/test_web_ui_contract.py`

**Interfaces:**
- Produces: `is_literal_confirmation(value: object) -> bool`.
- Produces: `require_mutation_confirmation(confirm: object, *, code: str, message: str, risk_flags: tuple[str, ...], context: dict[str, object] | None = None) -> dict[str, Any] | None`.
- Changes: `WorkHunter.hh_api_lab_call` adds keyword-only `confirm: bool = False` and keeps its `dict[str, Any]` return type.

- [ ] **Step 1: Add failing service and HTTP regression tests**

Add to `tests/test_hh_api_lab.py`:

```python
import pytest


@pytest.mark.parametrize("value", [None, False, "true", "false", 1, 0, [], {}])
def test_hh_api_lab_mutation_requires_literal_true(monkeypatch, tmp_path, value):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHApiLabClient)
    FakeHHApiLabClient.requests = []
    app = WorkHunter(tmp_path)

    result = app.hh_api_lab_call(
        method="POST",
        path="/negotiations",
        body={"vacancy_id": "vac-1"},
        confirm=value,
    )

    assert result["status"] == "blocked"
    assert result["code"] == "hh_api_lab_mutation_requires_confirmation"
    assert result["requires_confirmation"] is True
    assert "hh_api_mutation" in result["risk_flags"]
    assert FakeHHApiLabClient.requests == []


def test_hh_api_lab_confirmed_mutation_is_masked_and_audited(monkeypatch, tmp_path):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHApiLabClient)
    FakeHHApiLabClient.requests = []
    app = WorkHunter(tmp_path)
    app.config["sources"]["hh"]["access_token"] = "token"

    result = app.hh_api_lab_call(
        method="POST",
        path="/negotiations",
        body={"access_token": "secret"},
        confirm=True,
    )

    assert result["status"] == "ok"
    assert result["confirmed_by_user"] is True
    assert result["body"]["access_token"] == "***"
    assert FakeHHApiLabClient.requests[0]["method"] == "POST"
    assert FakeHHApiLabClient.requests[0]["data"] == {"access_token": "secret"}
    log = app.storage.list_hh_operation_logs()[-1]
    assert log.payload["body"]["access_token"] == "***"
    assert log.payload["confirmed_by_user"] is True


def test_hh_api_lab_http_rejects_string_confirmation_without_transport(
    monkeypatch, tmp_path
):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHApiLabClient)
    FakeHHApiLabClient.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(tmp_path))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = _post_json(
            f"http://127.0.0.1:{server.server_port}",
            "/api/hh/lab/call",
            {"method": "POST", "path": "/negotiations", "confirm": "false"},
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    assert result["status"] == "blocked"
    assert FakeHHApiLabClient.requests == []
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```powershell
pytest tests/test_hh_api_lab.py::test_hh_api_lab_mutation_requires_literal_true tests/test_hh_api_lab.py::test_hh_api_lab_confirmed_mutation_is_masked_and_audited tests/test_hh_api_lab.py::test_hh_api_lab_http_rejects_string_confirmation_without_transport -q
```

Expected: failures because `hh_api_lab_call` has no `confirm` parameter and currently dispatches mutating methods.

- [ ] **Step 3: Implement the shared guard and use it at every service/HTTP boundary**

Create `work_hunter/safety.py`:

```python
from __future__ import annotations

from typing import Any


READ_ONLY_HTTP_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def is_literal_confirmation(value: object) -> bool:
    return value is True


def require_mutation_confirmation(
    confirm: object,
    *,
    code: str,
    message: str,
    risk_flags: tuple[str, ...],
    context: dict[str, object] | None = None,
) -> dict[str, Any] | None:
    if is_literal_confirmation(confirm):
        return None
    return {
        "status": "blocked",
        "code": code,
        "message": message,
        "requires_confirmation": True,
        "risk_flags": list(risk_flags),
        **(context or {}),
    }
```

In `hh_api_lab_call`, normalize the request first, construct the masked `safe_input`, and block before client construction:

```python
safe_input = {
    "method": request["method"],
    "path": request["path"],
    "params": mask_secrets(request.get("params") or {}),
    "body": mask_secrets(request.get("body") or {}),
    "quick": quick,
    "confirmed_by_user": is_literal_confirmation(confirm),
}
if request["method"] not in READ_ONLY_HTTP_METHODS:
    blocked = require_mutation_confirmation(
        confirm,
        code="hh_api_lab_mutation_requires_confirmation",
        message="Mutating HH API Lab calls require explicit confirmation.",
        risk_flags=("hh_api_mutation", "external_mutating_request"),
        context=safe_input,
    )
    if blocked is not None:
        return blocked
```

Delete the duplicate `HH_API_READ_ONLY_METHODS` constant from `services.py` and import `READ_ONLY_HTTP_METHODS` from `work_hunter.safety`; `HH_API_LAB_ALLOWED_METHODS` remains the syntax allowlist.

After `start_hh_agent_mcp_run`, append a masked operation log before and after dispatch. Replace every `confirm=bool(body.get("confirm"))` and `confirm_apply=bool(...)` in `work_hunter/web/server.py` with `is_literal_confirmation(...)`.

In `work_hunter/web/static/app.js`, add:

```javascript
const HH_LAB_MUTATING_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

function confirmHhLabMutation(payload) {
  if (!HH_LAB_MUTATING_METHODS.has(payload.method.toUpperCase())) return true;
  if (!window.confirm(`HH API mutation: ${payload.method} ${payload.path}. Continue?`)) return false;
  return window.confirm("Final confirmation: this can change your HH account.");
}
```

Before the Lab POST, return when the helper is false and set `payload.confirm = true` only after both confirmations.

- [ ] **Step 4: Run focused and adjacent safety tests**

Run:

```powershell
pytest tests/test_hh_api_lab.py tests/test_hh_operations.py tests/test_apply_plan.py tests/test_v2_workflows.py tests/test_web_ui_contract.py -q
```

Expected: PASS; no fake transport receives a mutation without literal confirmation.

- [ ] **Step 5: Commit the API Lab safety boundary**

```powershell
git add work_hunter/safety.py work_hunter/services.py work_hunter/web/server.py work_hunter/web/static/app.js tests/test_hh_api_lab.py tests/test_web_ui_contract.py
git commit -m "fix: guard HH API mutations"
```

### Task 2: Loopback-Only HTTP Request Boundary And Security Headers

**Files:**
- Create: `work_hunter/web/security.py`
- Modify: `work_hunter/web/server.py` (`run_server`, handler `do_POST`, `do_OPTIONS`, `end_headers`)
- Create: `tests/test_web_security.py`

**Interfaces:**
- Produces: `ensure_loopback_listener(host: str) -> None`.
- Produces: `request_boundary_error` with the full keyword-only signature shown in Step 3 and return type `tuple[HTTPStatus, str, str] | None`.
- Produces: `SECURITY_HEADERS: dict[str, str]`.

- [ ] **Step 1: Write failing request-boundary tests**

Create `tests/test_web_security.py` with a loopback server fixture and `http.client.HTTPConnection` helper. Cover these exact cases:

```python
def test_non_loopback_bind_is_rejected_before_server_creation():
    with pytest.raises(ValueError, match="loopback"):
        ensure_loopback_listener("0.0.0.0")


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
    assert raw_request(cockpit, "POST", "/api/score", {"Host": host, "Content-Type": "application/json"}, b"{}")[0] == 200


def test_all_responses_include_security_headers(cockpit):
    code, headers, _ = raw_request(cockpit, "GET", "/", {}, None)
    assert code == 200
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
    assert headers["referrer-policy"] == "no-referrer"
```

- [ ] **Step 2: Run the new module and verify RED**

Run: `pytest tests/test_web_security.py -q`

Expected: import failure for `work_hunter.web.security`.

- [ ] **Step 3: Implement the pure security helpers and enforce them before body parsing**

Create `work_hunter/web/security.py`:

```python
from __future__ import annotations

import ipaddress
from http import HTTPStatus
from urllib.parse import urlsplit


SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}


def _host_and_port(value: str) -> tuple[str, int | None]:
    raw = value.strip()
    if raw == "::1":
        return "::1", None
    parsed = urlsplit(f"//{raw}")
    return (parsed.hostname or "").lower(), parsed.port


def _is_loopback(value: str) -> bool:
    host, _ = _host_and_port(value)
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def ensure_loopback_listener(host: str) -> None:
    if not _is_loopback(host):
        raise ValueError("Work Hunter UI must bind to a loopback host")


def request_boundary_error(
    *,
    method: str,
    host_header: str,
    origin_header: str | None,
    content_type: str | None,
    listener_host: str,
    listener_port: int,
) -> tuple[HTTPStatus, str, str] | None:
    host, host_port = _host_and_port(host_header)
    if not _is_loopback(host) or host_port != listener_port:
        return HTTPStatus.FORBIDDEN, "host_not_loopback", "Loopback Host header required."
    if origin_header:
        origin = urlsplit(origin_header)
        origin_port = origin.port or (443 if origin.scheme == "https" else 80)
        if origin.scheme != "http" or not _is_loopback(origin.netloc) or origin_port != listener_port:
            return HTTPStatus.FORBIDDEN, "cross_origin_request", "Cross-origin requests are blocked."
    if method.upper() in {"POST", "PUT", "PATCH", "DELETE"}:
        media_type = (content_type or "").split(";", 1)[0].strip().lower()
        if media_type != "application/json":
            return HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "json_required", "application/json is required."
    return None
```

Call `ensure_loopback_listener(host)` before `ThreadingHTTPServer(...)`. In `do_POST`, call `request_boundary_error` before `_read_json()` and `WorkHunter(root)`. Override `end_headers()` to emit `SECURITY_HEADERS`. Implement `do_OPTIONS()` as `405 Method Not Allowed` without CORS authorization headers.

- [ ] **Step 4: Run HTTP and UI contract suites**

Run:

```powershell
pytest tests/test_web_security.py tests/test_web_ui_contract.py tests/test_hh_agent_http_api.py tests/test_hh_onboarding.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit the local HTTP boundary**

```powershell
git add work_hunter/web/security.py work_hunter/web/server.py tests/test_web_security.py
git commit -m "fix: harden local cockpit requests"
```

### Task 3: Mask-Preserving Configuration Updates And Explicit Secret Clear

**Files:**
- Modify: `work_hunter/config.py`
- Modify: `work_hunter/services.py`
- Modify: `work_hunter/web/server.py`
- Test: `tests/test_config.py`
- Test: `tests/test_web_security.py`

**Interfaces:**
- Produces: `is_sensitive_key(key: str) -> bool`.
- Produces: `merge_masked_config(stored: dict[str, Any], submitted: dict[str, Any]) -> dict[str, Any]`.
- Produces: `clear_config_secret_value(config: dict[str, Any], path: str) -> dict[str, Any]`.
- Produces: `WorkHunter.update_config_from_client(patch)` and `WorkHunter.clear_config_secret(path, *, confirm=False)`.

- [ ] **Step 1: Add failing unit and HTTP round-trip tests**

Add to `tests/test_config.py`:

```python
def test_merge_masked_config_preserves_nested_secrets_for_mask_empty_and_none():
    stored = {
        "ai": {"api_key": "real-ai", "model": "model-a"},
        "sources": {"hh": {"access_token": "real-hh", "refresh_token": "refresh"}},
    }
    submitted = {
        "ai": {"api_key": "***", "model": "model-b"},
        "sources": {"hh": {"access_token": "", "refresh_token": None}},
    }

    merged = merge_masked_config(stored, submitted)

    assert merged["ai"] == {"api_key": "real-ai", "model": "model-b"}
    assert merged["sources"]["hh"] == {
        "access_token": "real-hh",
        "refresh_token": "refresh",
    }
```

Add to `tests/test_web_security.py`:

```python
def test_masked_config_http_round_trip_never_persists_mask(cockpit, tmp_path):
    app = WorkHunter(tmp_path)
    app.config["ai"]["api_key"] = "REAL_AI_KEY"
    app.config["sources"]["hh"]["access_token"] = "REAL_HH_TOKEN"
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
    assert reloaded.config["ai"]["api_key"] == "REAL_AI_KEY"
    assert reloaded.config["sources"]["hh"]["access_token"] == "REAL_HH_TOKEN"


@pytest.mark.parametrize("confirm", [None, False, "true", 1])
def test_clear_config_secret_requires_literal_true(tmp_path, confirm):
    app = WorkHunter(tmp_path)
    app.config["sources"]["hh"]["access_token"] = "token"
    result = app.clear_config_secret(
        "sources.hh.access_token",
        confirm=confirm,
    )
    assert result["status"] == "blocked"
    assert app.config["sources"]["hh"]["access_token"] == "token"


def test_clear_config_secret_rejects_unknown_path_and_clears_only_selected(tmp_path):
    app = WorkHunter(tmp_path)
    app.config["sources"]["hh"].update({
        "access_token": "access",
        "refresh_token": "refresh",
    })
    rejected = app.clear_config_secret("sources.hh.enabled", confirm=True)
    cleared = app.clear_config_secret("sources.hh.access_token", confirm=True)
    assert rejected["status"] == "blocked"
    assert cleared == {"status": "ok", "path": "sources.hh.access_token"}
    assert app.config["sources"]["hh"]["access_token"] == ""
    assert app.config["sources"]["hh"]["refresh_token"] == "refresh"
```

- [ ] **Step 2: Run config tests and verify RED**

Run: `pytest tests/test_config.py tests/test_web_security.py -q`

Expected: masked round-trip persists `***`; clear helpers do not exist.

- [ ] **Step 3: Implement merge, allowlist, service methods, and route**

In `work_hunter/config.py`, reuse the same sensitivity predicate for masking and merging:

```python
def is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return lowered in SENSITIVE_EXACT or any(
        lowered.endswith(suffix) for suffix in SENSITIVE_SUFFIXES
    )


def merge_masked_config(stored: dict[str, Any], submitted: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(stored)
    for key, value in submitted.items():
        if is_sensitive_key(key) and (value is None or value == "" or value == MASK):
            continue
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = merge_masked_config(current, value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged
```

Define exact top-level allowlisted paths plus validation for existing `hh_account_profiles.<name>.(access_token|refresh_token|client_secret)`. `clear_config_secret_value` deep-copies the config, walks the validated path, and writes `""` only at the selected leaf.

In `WorkHunter`, merge into `self.config`, save atomically through the existing config save function, and return `mask_secrets(self.config)`. The clear method uses `require_mutation_confirmation` and returns only status/path. Route `POST /api/config/secret/clear` before general `/api/config` handling.

- [ ] **Step 4: Run config, HTTP, and secret scans**

Run:

```powershell
pytest tests/test_config.py tests/test_web_security.py tests/test_web_ui_contract.py -q
git grep -n -I -E "(REAL_AI_KEY|REAL_HH_TOKEN|sk-[A-Za-z0-9_-]{20,})" -- work_hunter tests
```

Expected: tests PASS; secret scan has no output.

- [ ] **Step 5: Commit safe config updates**

```powershell
git add work_hunter/config.py work_hunter/services.py work_hunter/web/server.py tests/test_config.py tests/test_web_security.py
git commit -m "fix: preserve masked configuration secrets"
```

### Task 4: Safe-By-Default Resume Mutations

**Files:**
- Modify: `work_hunter/services.py` (`update_hh_resumes`, create/from-file/clone methods)
- Modify: `work_hunter/cli.py` (resume flags and dispatch)
- Modify: `work_hunter/web/server.py` (`/api/hh/resumes/update`)
- Test: `tests/test_hh_operations.py`
- Test: `tests/test_hh_resume_operations.py`

**Interfaces:**
- Changes: `update_hh_resumes(*, confirm: bool = False)`.
- Changes: create/from-file/clone default `dry_run=True` and accept `confirm=False`.

- [ ] **Step 1: Rewrite current live-default tests as failing safety contracts**

Add to `tests/test_hh_resume_operations.py`:

```python
def test_create_hh_resume_defaults_to_dry_run_without_transport(monkeypatch, tmp_path):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHResumeClient)
    FakeHHResumeClient.created_payloads = []
    app = WorkHunter(tmp_path)
    app.config["sources"]["hh"]["access_token"] = "token"
    result = app.create_hh_resume({"title": "Python Backend"})
    assert result == {"status": "dry_run", "payload": {"title": "Python Backend"}}
    assert FakeHHResumeClient.created_payloads == []


@pytest.mark.parametrize("confirm", [None, False, "true", "false", 1])
def test_create_hh_resume_real_mode_rejects_nonliteral_confirmation(
    monkeypatch, tmp_path, confirm
):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHResumeClient)
    FakeHHResumeClient.created_payloads = []
    app = WorkHunter(tmp_path)
    result = app.create_hh_resume(
        {"title": "Python Backend"},
        dry_run=False,
        confirm=confirm,
    )
    assert result["status"] == "blocked"
    assert FakeHHResumeClient.created_payloads == []


def test_update_hh_resumes_blocks_before_transport_without_confirmation(
    monkeypatch, tmp_path
):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHResumeClient)
    app = WorkHunter(tmp_path)
    result = app.update_hh_resumes()
    assert result["status"] == "blocked"
    assert result["code"] == "resume_mutation_requires_confirmation"
```

Add these entry-point tests:

```python
def test_resume_cli_is_dry_run_by_default_and_requires_real_plus_confirm(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHResumeClient)
    app = WorkHunter(tmp_path)
    app.config["sources"]["hh"]["access_token"] = "token"
    app.save_config(app.config)
    arguments = [
        "--root", str(tmp_path), "hh-create-resume",
        "--payload", '{"title":"Python Backend"}',
    ]
    cli_main(arguments)
    assert json.loads(capsys.readouterr().out)["status"] == "dry_run"
    cli_main([*arguments, "--real"])
    assert json.loads(capsys.readouterr().out)["status"] == "blocked"


def test_resume_http_update_rejects_string_confirmation(monkeypatch, tmp_path):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeHHOperationsClient)
    FakeHHOperationsClient.updated_resumes = []
    app = WorkHunter(tmp_path)
    app.config["sources"]["hh"]["access_token"] = "token"
    app.save_config(app.config)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(tmp_path))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = _post_json(
            f"http://127.0.0.1:{server.server_port}",
            "/api/hh/resumes/update",
            {"confirm": "false"},
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
    assert result["status"] == "blocked"
    assert FakeHHOperationsClient.updated_resumes == []
```

Use the confirmed publish call exactly as follows:

```python
result = app.create_hh_resume(
    {"title": "Python Backend"},
    dry_run=False,
    publish=True,
    confirm=True,
)
assert result["publish_result"] == {
    "status": "published",
    "resume_id": "new-resume",
}
```

- [ ] **Step 2: Run resume suites and verify RED**

Run:

```powershell
pytest tests/test_hh_resume_operations.py tests/test_hh_operations.py -q
```

Expected: defaults currently dispatch transport and update has no confirm parameter.

- [ ] **Step 3: Put service guards before client construction and wire CLI/HTTP flags**

Add keyword-only `confirm: bool = False` to `update_hh_resumes`. Change the defaults of `create_hh_resume`, `create_hh_resume_from_file`, and `clone_hh_resume` to `dry_run: bool = True`, and add keyword-only `confirm: bool = False` to each.

Put this complete guard block before client construction in each real-mode path:

```python
if not dry_run:
    blocked = require_mutation_confirmation(
        confirm,
        code="resume_mutation_requires_confirmation",
        message="HH resume mutations require explicit confirmation.",
        risk_flags=("resume_mutation", "external_mutating_request"),
    )
    if blocked is not None:
        return blocked
```

`update_hh_resumes` always executes this same block without the `if not dry_run` wrapper because the method is inherently live.

For any real mode, call `require_mutation_confirmation` before constructing `HHApplyClient`. Add `--confirm` to `hh-update-resumes`; replace create/clone `--dry-run` semantics with explicit `--real` plus `--confirm`. Keep legacy `--dry-run` accepted as a hidden compatibility alias if tests or docs still use it. The HTTP update route passes `is_literal_confirmation(body.get("confirm"))`.

- [ ] **Step 4: Run all resume and CLI contracts**

Run:

```powershell
pytest tests/test_hh_resume_operations.py tests/test_hh_operations.py tests/test_cli_contract.py tests/test_hh_agent_http_api.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit resume mutation gates**

```powershell
git add work_hunter/services.py work_hunter/cli.py work_hunter/web/server.py tests/test_hh_operations.py tests/test_hh_resume_operations.py tests/test_cli_contract.py
git commit -m "fix: require confirmation for resume mutations"
```

### Task 5: Safety Integration Gate

**Files:**
- Modify only if a focused regression exposes a defect in files already owned by Tasks 1-4.

**Interfaces:**
- Consumes all safety helpers and guarded service signatures from Tasks 1-4.
- Produces a green safety slice with no real network calls.

- [ ] **Step 1: Run the complete safety slice**

```powershell
pytest tests/test_hh_api_lab.py tests/test_web_security.py tests/test_config.py tests/test_hh_resume_operations.py tests/test_hh_operations.py tests/test_hh_agent_http_api.py tests/test_web_ui_contract.py tests/test_mcp_safety.py -q
```

Expected: PASS.

- [ ] **Step 2: Run static and repository hygiene checks**

```powershell
ruff check work_hunter tests
mypy work_hunter/safety.py work_hunter/web/security.py work_hunter/config.py work_hunter/web/server.py
git diff --check
```

Expected: all commands exit zero for the safety-owned files; repository-wide historical mypy issues may remain until the release plan.

- [ ] **Step 3: Review the diff for unguarded live paths**

```powershell
rg.exe -n "confirm=bool\(|confirm_apply=bool\(|def hh_api_lab_call|def update_hh_resumes|def create_hh_resume" work_hunter
```

Expected: no `confirm=bool(...)` or `confirm_apply=bool(...)`; guarded service definitions remain visible.

- [ ] **Step 4: Commit only if integration required a correction**

```powershell
git add work_hunter tests
git commit -m "test: complete safety regression gate"
```

Skip this commit when Step 1-3 required no file changes.
