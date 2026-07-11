# Work Hunter 1.0 Browser Cockpit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the local cockpit non-destructive, keyboard-usable, offline, error-tolerant, and executable under a real headless browser test.

**Architecture:** Keep vanilla HTML/CSS/JavaScript and the standard-library HTTP server. Introduce small client helpers for explicit IDs, form state, safe DOM insertion, busy state, and isolated loading; make the API return canonical note/stats shapes; verify behavior in Chromium against an ephemeral loopback server.

**Tech Stack:** Vanilla JavaScript, HTML/CSS, Python `http.server`, pytest, Playwright Chromium.

## Global Constraints

- UI runtime must make no request to a non-loopback origin.
- Opening `/agent` must not make a credentialed HH request.
- Editing a local resume must never clear fields or deactivate it unless explicitly selected by the user.
- Dynamic API text must render as text, never executable markup.
- All browser tests use a temporary database and fake/intercepted network responses.
- No React/build-tool migration and no remote font/icon dependency.

---

### Task 1: Establish The Headless Browser Harness

**Files:**
- Create: `tests/test_web_ui_browser.py`
- Modify: `pyproject.toml` (pytest marker only if required)

**Interfaces:**
- Produces: `browser_app` fixture yielding `(page, base_url, app)`.
- Captures page and console errors; teardown fails on either. Task 5 records request URLs and enforces the loopback-only rule after remote assets are removed.

- [ ] **Step 1: Write the initial browser smoke test**

Create `tests/test_web_ui_browser.py`:

```python
from __future__ import annotations

import json
import threading
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import expect, sync_playwright

from work_hunter.services import WorkHunter
from work_hunter.web.server import make_handler
from http.server import ThreadingHTTPServer


@pytest.fixture
def browser_app(tmp_path):
    app = WorkHunter(tmp_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(tmp_path))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    page_errors: list[str] = []
    console_errors: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.on(
            "console",
            lambda message: console_errors.append(message.text)
            if message.type == "error"
            else None,
        )
        yield page, base_url, app
        browser.close()
    server.shutdown()
    thread.join(timeout=5)
    server.server_close()
    assert page_errors == []
    assert console_errors == []


def test_ui_loads_without_browser_errors(browser_app):
    page, base_url, _ = browser_app
    page.goto(base_url, wait_until="networkidle")
    expect(page.locator("#jobs-body")).to_be_visible()
    expect(page.locator("body")).not_to_have_attribute("aria-busy", "true")
```

- [ ] **Step 2: Install Chromium and run the test**

```powershell
python -m playwright install chromium
pytest tests/test_web_ui_browser.py::test_ui_loads_without_browser_errors -q
```

Expected: baseline may fail on an uncaught page or console error; record the exact failure for its owning task.

- [ ] **Step 3: Make only harness-level corrections**

Fix fixture lifecycle or allowed `data:`/loopback URL handling only. Do not suppress page/console errors and do not allow arbitrary `https:` origins.

- [ ] **Step 4: Re-run and keep the first product failure visible**

Run the command from Step 2.

Expected: fixture is stable. A product failure is allowed until its owning task, but the test must not skip.

- [ ] **Step 5: Commit the browser harness with the first behavior task, not alone**

Do not commit a permanently red harness. Include it in Task 2's commit after the first UI regressions are green.

### Task 2: Non-Destructive Resume Edit And Explicit Ghost Target

**Files:**
- Modify: `work_hunter/web/static/app.js` (state, resume and status helpers)
- Test: `tests/test_web_ui_browser.py`
- Test: `tests/test_web_ui_contract.py`

**Interfaces:**
- Produces: `resetResumeForm()`, `resumePayloadFromForm()`, `updateJobStatus(jobId, status)`, and `markGhostJob(jobId)`.
- Changes: `state.resumes` is the source of truth for the edit form.

- [ ] **Step 1: Add failing browser tests**

Add imports for `Resume` and seed one active resume. Add:

```python
def test_resume_edit_populates_fields_and_preserves_active(browser_app):
    page, base_url, app = browser_app
    resume_id = app.storage.save_resume(
        Resume(
            name="Backend CV",
            body="Python and PostgreSQL",
            profile_id="default",
            is_active=True,
        )
    )
    page.goto(f"{base_url}/settings", wait_until="networkidle")
    page.get_by_role("button", name="Ред.").click()
    expect(page.locator("#resume-name-input")).to_have_value("Backend CV")
    expect(page.locator("#resume-body-input")).to_have_value("Python and PostgreSQL")
    with page.expect_response(lambda response: response.url.endswith("/api/resumes")):
        page.locator("#save-resume-button").click()

    saved = app.storage.get_resume(resume_id)
    assert saved.name == "Backend CV"
    assert saved.body == "Python and PostgreSQL"
    assert saved.is_active is True
```

Add:

```python
def test_ghost_action_updates_rendered_job_not_selected_job(browser_app):
    page, base_url, _ = browser_app
    status_urls: list[str] = []
    page.route(
        "**/api/ghost-jobs?days=7",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body='[{"id":11,"title":"A","company":"One","source":"x"},'
                 '{"id":22,"title":"B","company":"Two","source":"x"}]',
        ),
    )

    def capture_status(route):
        status_urls.append(route.request.url)
        route.fulfill(status=200, content_type="application/json", body='{"status":"ok"}')

    page.route("**/api/jobs/*/status", capture_status)
    page.goto(f"{base_url}/settings", wait_until="networkidle")
    page.evaluate("state.selectedId = 11")
    page.locator("#check-ghost-button").click()
    page.locator("#ghost-jobs-list .source-row").nth(1).get_by_role(
        "button", name="Отметить ghosted"
    ).click()
    page.wait_for_timeout(100)
    assert any(url.endswith("/api/jobs/22/status") for url in status_urls)
    assert not any(url.endswith("/api/jobs/11/status") for url in status_urls)
```

- [ ] **Step 2: Verify RED**

Run:

```powershell
pytest tests/test_web_ui_browser.py::test_resume_edit_populates_fields_and_preserves_active tests/test_web_ui_browser.py::test_ghost_action_updates_rendered_job_not_selected_job -q
```

Expected: resume fields are empty/active becomes false; ghost action targets global `state.selectedId`.

- [ ] **Step 3: Implement explicit form and job state**

Extend state with `resumes: []`. Replace the resume helpers with:

```javascript
let editingResumeId = 0;

function resetResumeForm() {
  editingResumeId = 0;
  $("#resume-name-input").value = "";
  $("#resume-body-input").value = "";
}

function showResumeForm(id = 0) {
  editingResumeId = Number(id) || 0;
  const resume = state.resumes.find((item) => Number(item.id) === editingResumeId);
  $("#resume-name-input").value = resume?.name || "";
  $("#resume-body-input").value = resume?.body || "";
  $("#resume-form").style.display = "block";
  $("#resume-name-input").focus();
}

function resumePayloadFromForm() {
  const original = state.resumes.find((item) => Number(item.id) === editingResumeId);
  return {
    id: editingResumeId,
    name: $("#resume-name-input").value.trim(),
    body: $("#resume-body-input").value,
    profile_id: original?.profile_id || state.profile?.active || "default",
    is_active: original?.is_active === true,
    ats_score: original?.ats_score ?? null,
  };
}
```

`loadResumes()` assigns `state.resumes = resumes`. `saveResume()` uses `resumePayloadFromForm()` and resets only after success.

Replace status helpers with:

```javascript
async function updateJobStatus(jobId, status) {
  const id = requirePositiveInteger(jobId, "job id");
  await api(`/api/jobs/${id}/status`, {
    method: "POST",
    body: JSON.stringify({ status }),
  });
}

async function markSelected(status) {
  if (!state.selectedId) return;
  await updateJobStatus(state.selectedId, status);
  await loadJobs();
}

async function markGhostJob(jobId) {
  await updateJobStatus(jobId, "ghosted");
  await loadGhostJobs();
}
```

Render the ghost action with its row ID.

- [ ] **Step 4: Run browser and static UI contracts**

```powershell
pytest tests/test_web_ui_browser.py::test_resume_edit_populates_fields_and_preserves_active tests/test_web_ui_browser.py::test_ghost_action_updates_rendered_job_not_selected_job tests/test_web_ui_contract.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit UI state correctness**

```powershell
git add work_hunter/web/static/app.js tests/test_web_ui_browser.py tests/test_web_ui_contract.py
git commit -m "fix: preserve explicit UI record targets"
```

### Task 3: Dry Agent Load, Canonical Stats, And Favorite Notes

**Files:**
- Modify: `work_hunter/web/static/index.html`
- Modify: `work_hunter/web/static/app.js`
- Modify: `work_hunter/web/server.py`
- Test: `tests/test_web_ui_contract.py`
- Test: `tests/test_web_ui_browser.py`

**Interfaces:**
- Produces: `loadAgentCockpit({liveAuth = false} = {})`, `loadAgentPreflight({liveAuth = false} = {})`, and `checkAgentLiveAuth()`.
- Produces: `_job_json(job, storage, *, note_preview_chars=120)`.
- Produces: ordered `application_funnel` and client `funnelWidth(count, total)`.

- [ ] **Step 1: Add failing API/static/browser tests**

Add to `tests/test_web_ui_contract.py`:

```python
def test_stats_api_uses_one_application_status_mapping(tmp_path):
    app = WorkHunter(tmp_path)
    for index, status in enumerate(("applied", "response", "interview", "offer"), 1):
        job_id = app.storage.upsert_job(
            Job(source="x", source_id=str(index), url=f"https://x/{index}", title=status)
        )
        app.storage.set_status(job_id, status)
    stats = _compute_stats(app.list_jobs(limit=20), app.storage)
    funnel = {item["status"]: item["count"] for item in stats["application_funnel"]}
    assert stats["total_applications"] == 4
    assert funnel["applied"] == 1
    assert funnel["response"] == 1
    assert funnel["interview"] == 1
    assert funnel["offer"] == 1


def test_jobs_api_includes_bounded_note_preview(tmp_path):
    app = WorkHunter(tmp_path)
    job_id = app.storage.upsert_job(
        Job(source="x", source_id="noted", url="https://x/noted", title="Noted")
    )
    app.storage.save_note(job_id, "n" * 200)
    payload = _job_json(app.get_job(job_id), app.storage)
    assert payload["note_preview"] == "n" * 120
```

Add browser interception:

```python
def test_agent_load_is_dry_until_explicit_live_auth_click(browser_app):
    page, base_url, _ = browser_app
    preflight_urls: list[str] = []
    page.on(
        "request",
        lambda request: preflight_urls.append(request.url)
        if "/api/agent/preflight" in request.url
        else None,
    )
    page.goto(f"{base_url}/agent", wait_until="networkidle")
    assert preflight_urls
    assert all("live_auth=true" not in url for url in preflight_urls)
    page.locator("#agent-live-auth-button").click()
    page.wait_for_timeout(100)
    assert sum("live_auth=true" in url for url in preflight_urls) == 1
```

- [ ] **Step 2: Verify RED**

Run:

```powershell
pytest tests/test_web_ui_contract.py::test_stats_api_uses_one_application_status_mapping tests/test_web_ui_contract.py::test_jobs_api_includes_bounded_note_preview tests/test_web_ui_browser.py::test_agent_load_is_dry_until_explicit_live_auth_click -q
```

Expected: funnel fields disagree, note field is absent, and agent load calls `live_auth=true` automatically.

- [ ] **Step 3: Implement canonical API shapes and explicit live auth**

Add an `#agent-live-auth-button` labelled `Проверить HH auth` beside refresh. Default cockpit and refresh calls use `liveAuth: false`; only its click handler invokes `checkAgentLiveAuth()`.

Define one server mapping:

```python
APPLICATION_FUNNEL_STAGES = (
    "applied", "response", "phone_screen", "interview", "offer", "rejected"
)


def _application_stats(by_status: dict[str, int]) -> tuple[int, list[dict[str, object]]]:
    funnel = [
        {"status": status, "count": by_status.get(status, 0)}
        for status in APPLICATION_FUNNEL_STAGES
    ]
    return sum(int(item["count"]) for item in funnel), funnel
```

Return `application_funnel` and derive `total_applications` from it. Render that array directly and cap widths:

```javascript
function funnelWidth(count, total) {
  if (!total) return 0;
  return Math.min(100, Math.max(0, (Number(count) / Number(total)) * 100));
}
```

Serialize list jobs with:

```python
def _job_json(job: Any, storage: Any, *, note_preview_chars: int = 120) -> dict[str, Any]:
    payload = job.to_dict()
    note = storage.get_note(int(job.id or 0))
    payload["note_preview"] = note[:note_preview_chars]
    return payload
```

Favorites read `note_preview`, not `note_short`.

- [ ] **Step 4: Run API and browser tests**

```powershell
pytest tests/test_web_ui_contract.py tests/test_web_ui_browser.py::test_agent_load_is_dry_until_explicit_live_auth_click tests/test_hh_onboarding.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit canonical cockpit data**

```powershell
git add work_hunter/web/server.py work_hunter/web/static/index.html work_hunter/web/static/app.js tests/test_web_ui_contract.py tests/test_web_ui_browser.py
git commit -m "fix: make cockpit data flows explicit"
```

### Task 4: Keyboard Rows, Busy Restoration, And Isolated Loading

**Files:**
- Modify: `work_hunter/web/static/index.html`
- Modify: `work_hunter/web/static/app.js`
- Test: `tests/test_web_ui_browser.py`

**Interfaces:**
- Produces: `bindJobRowActivation`, `setBusy(target, busy, busyLabel)`, `runIsolatedLoad`, and loading/error helpers.

- [ ] **Step 1: Add failing keyboard, busy, and initialization tests**

Add these browser tests (the fixture seeds a Python job before navigation):

```python
def test_job_row_opens_with_enter_and_checkbox_space_stays_independent(browser_app):
    page, base_url, app = browser_app
    app.storage.upsert_job(
        Job(source="x", source_id="keyboard", url="https://x/keyboard", title="Python")
    )
    page.goto(base_url, wait_until="networkidle")
    row = page.locator("#jobs-body tr").first
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
            route.fulfill(status=200, content_type="application/json", body='{"ok":true}')
        else:
            route.fulfill(status=200, content_type="application/json", body='{invalid')

    page.route("**/parse-structure", result)
    page.on("dialog", lambda dialog: dialog.dismiss())
    page.goto(base_url, wait_until="networkidle")
    page.locator("#jobs-body tr").first.click()
    button = page.get_by_role("button", name="Структура")
    original = button.text_content()
    button.click()
    expect(button).to_have_text(original)
    button.click()
    expect(button).to_have_text(original)


def test_initial_source_failure_does_not_abort_jobs(browser_app):
    page, base_url, app = browser_app
    app.storage.upsert_job(
        Job(source="x", source_id="still-loads", url="https://x/job", title="Still loads")
    )
    page.route(
        "**/api/sources",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body='{invalid',
        ),
    )
    page.goto(base_url, wait_until="networkidle")
    expect(page.locator("#jobs-body")).to_contain_text("Still loads")
    expect(page.locator("#init-errors")).to_be_visible()
    expect(page.locator("#init-errors button")).to_be_visible()
```

- [ ] **Step 2: Verify RED**

Run the four new tests in `tests/test_web_ui_browser.py`.

Expected: rows are not focusable, dynamic button label sticks, and a rejected initial promise aborts initialization.

- [ ] **Step 3: Implement reusable interaction/load helpers**

```javascript
function bindJobRowActivation(row, job) {
  row.tabIndex = 0;
  row.setAttribute("role", "button");
  row.setAttribute("aria-label", `Открыть ${job.title || "вакансию"}`);
  const activate = () => selectJob(requirePositiveInteger(job.id, "job id"));
  row.addEventListener("click", (event) => {
    if (!event.target.closest("input,button,a")) activate();
  });
  row.addEventListener("keydown", (event) => {
    if ((event.key === "Enter" || event.key === " ") && !event.target.closest("input,button,a")) {
      event.preventDefault();
      activate();
    }
  });
}

const busyButtonLabels = new WeakMap();

function setBusy(target, busy, busyLabel = "Работаю...") {
  const button = typeof target === "string" ? document.querySelector(target) : target;
  if (!button) return;
  if (busy) {
    if (!busyButtonLabels.has(button)) busyButtonLabels.set(button, button.textContent);
    button.disabled = true;
    button.textContent = busyLabel;
  } else {
    button.disabled = false;
    button.textContent = busyButtonLabels.get(button) || button.textContent;
    busyButtonLabels.delete(button);
  }
}

async function runIsolatedLoad(name, loader) {
  try {
    clearSectionError(name);
    await loader();
  } catch (error) {
    showSectionError(name, error, () => runIsolatedLoad(name, loader));
  }
}
```

Wrap every busy call in `try/finally`. Replace the top-level `Promise.all` with `Promise.allSettled` over named `runIsolatedLoad` calls. Add `#init-errors` with `role="alert"`; toggle `aria-busy` and skeleton visibility around jobs loading.

- [ ] **Step 4: Run browser interaction suite**

Run: `pytest tests/test_web_ui_browser.py -q`

Expected: PASS for keyboard, busy, and isolated error behavior.

- [ ] **Step 5: Commit resilient UI interactions**

```powershell
git add work_hunter/web/static/index.html work_hunter/web/static/app.js tests/test_web_ui_browser.py
git commit -m "fix: make cockpit interactions resilient"
```

### Task 5: Escape Dynamic DOM And Remove Remote Runtime Assets

**Files:**
- Modify: `work_hunter/web/static/index.html`
- Modify: `work_hunter/web/static/app.js`
- Modify: `work_hunter/web/static/app.css`
- Test: `tests/test_web_ui_contract.py`
- Test: `tests/test_web_ui_browser.py`

**Interfaces:**
- Produces: `requirePositiveInteger(value, fieldName="id")`.
- Dynamic actions use validated `data-*` values and listeners, not interpolated inline JavaScript.

- [ ] **Step 1: Add static and executable injection tests**

Add:

```python
def test_ui_static_assets_have_no_remote_runtime_dependencies():
    combined = "\n".join(
        _read_static(name) for name in ("index.html", "app.js", "app.css")
    )
    for forbidden in (
        "fonts.googleapis.com", "fonts.gstatic.com", "unpkg.com", "@latest"
    ):
        assert forbidden not in combined


def test_untrusted_api_text_cannot_create_markup_or_handlers(browser_app):
    page, base_url, _ = browser_app
    malicious = '<img id="pwned" src=x onerror="window.__pwned=1">'
    page.route(
        "**/api/jobs?*",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps([{
                "id": 1,
                "title": "Safe title",
                "company": "Company",
                "source": malicious,
                "status": "new",
                "score": None,
            }]),
        ),
    )
    page.goto(base_url, wait_until="networkidle")
    expect(page.locator("#jobs-body")).to_contain_text(malicious)
    expect(page.locator("#pwned")).to_have_count(0)
    assert page.evaluate("window.__pwned") is None


def test_ui_uses_loopback_runtime_requests_only(browser_app):
    page, base_url, _ = browser_app
    request_urls: list[str] = []
    page.on("request", lambda request: request_urls.append(request.url))
    page.goto(base_url, wait_until="networkidle")
    assert all(
        urlsplit(url).hostname in {None, "127.0.0.1", "localhost", "::1"}
        or url.startswith("data:")
        for url in request_urls
    )
```

- [ ] **Step 2: Verify RED**

Run:

```powershell
pytest tests/test_web_ui_contract.py::test_ui_static_assets_have_no_remote_runtime_dependencies tests/test_web_ui_browser.py::test_untrusted_api_text_cannot_create_markup_or_handlers -q
```

Expected: external assets are found and at least the unescaped stats source creates markup.

- [ ] **Step 3: Remove remote assets and make dynamic HTML safe**

Delete Google Font and unpkg tags. Use this system stack in CSS:

```css
font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont,
  "Segoe UI", sans-serif;
```

Remove decorative `<i data-lucide>` elements and make `refreshIcons()` a deleted call, not a no-op dependency. Use `textContent` for errors and simple fields. Where template strings remain, wrap every API-derived value with `escapeHtml`/`escapeAttr`. Replace inline dynamic actions with buttons carrying validated numeric `data-job-id`; one delegated listener calls named helpers.

Add:

```javascript
function requirePositiveInteger(value, fieldName = "id") {
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed) || parsed <= 0) {
    throw new Error(`Invalid ${fieldName}`);
  }
  return parsed;
}
```

- [ ] **Step 4: Run complete static and browser suites**

```powershell
pytest tests/test_web_ui_contract.py tests/test_web_ui_browser.py -q
```

Expected: PASS; browser fixture records no non-loopback request or uncaught JS error.

- [ ] **Step 5: Commit offline-safe rendering**

```powershell
git add work_hunter/web/static/index.html work_hunter/web/static/app.js work_hunter/web/static/app.css tests/test_web_ui_contract.py tests/test_web_ui_browser.py
git commit -m "fix: render cockpit data safely offline"
```

### Task 6: UI Slice Gate

**Files:**
- Modify only when the aggregate run exposes a UI-owned regression.

**Interfaces:**
- Consumes the final static/API/browser contracts.
- Produces a green UI slice with no remote runtime requests.

- [ ] **Step 1: Run all UI-facing tests**

```powershell
pytest tests/test_web_ui_contract.py tests/test_web_ui_browser.py tests/test_hh_agent_http_api.py tests/test_hh_onboarding.py tests/test_hh_api_lab.py -q
```

Expected: PASS.

- [ ] **Step 2: Validate JavaScript syntax and static URLs**

```powershell
node.exe --check work_hunter/web/static/app.js
rg.exe -n "https://|http://|@latest|onclick=\"" work_hunter/web/static
```

Expected: Node syntax check exits zero. Any remaining URL is an intentional user-facing placeholder/data link, not a remote script or stylesheet. Dynamic inline `onclick` handlers have been removed.

- [ ] **Step 3: Run lint/type/whitespace checks for owned files**

```powershell
ruff check work_hunter/web tests/test_web_ui_contract.py tests/test_web_ui_browser.py
mypy work_hunter/web
git diff --check
```

Expected: all commands exit zero.

- [ ] **Step 4: Commit only aggregate corrections**

```powershell
git add work_hunter/web tests/test_web_ui_contract.py tests/test_web_ui_browser.py
git commit -m "test: complete browser cockpit gate"
```

Skip this commit when aggregate verification changes no files.
