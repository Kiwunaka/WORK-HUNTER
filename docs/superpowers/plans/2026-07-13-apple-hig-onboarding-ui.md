# Apple HIG Onboarding UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the dense Work Hunter cockpit with a tested Apple HIG-inspired command center, three-step onboarding, Today dashboard, contextual guidance, consistent feedback overlays, and unchanged server-enforced HH safety.

**Architecture:** Keep the standard-library Python server and static frontend. Add two small backend compatibility boundaries, then split new browser behavior into ordered plain-script namespaces under `window.WorkHunterUI` so existing global `app.js` workflows and Playwright tests remain compatible. Migrate destinations incrementally while preserving every existing API/action and verifying desktop/mobile action parity.

**Tech Stack:** Python 3.11+, standard-library HTTP server, SQLite, plain HTML/CSS/JavaScript, pytest, Playwright Chromium, Ruff, mypy, setuptools package data.

## Global Constraints

- The application remains private, loopback-only, local-first, and single-user.
- Canonical destinations are `/today`, `/jobs`, `/applications`, `/calendar`, `/assistant`, `/analytics`, `/sources`, and `/settings`.
- Runtime UI assets remain local; add no remote fonts, scripts, telemetry, or analytics.
- Use system fonts beginning with `-apple-system` and `BlinkMacSystemFont`.
- Real HH mutations continue to require a literal JSON boolean `true`; client confirmation never replaces service/transport guards.
- Preserve masked-secret sentinel handling and use `/api/config/secret/clear` for explicit secret removal.
- Breakpoints are full sidebar at 961 px and above, icon sidebar at 721–960 px, and top-bar/menu plus single column at 720 px and below.
- Acceptance viewports are 1440×900, 900×900, and 680×844.
- All existing major capabilities remain reachable and receive stable `data-action-id` values on desktop and mobile.
- Motion respects `prefers-reduced-motion`; color is never the only status signal.
- Follow TDD: failing focused test, minimal implementation, passing focused test, then commit.

---

## File Structure

### Backend

- `work_hunter/config.py` — onboarding-version default and atomic legacy config/profile normalization.
- `work_hunter/web/server.py` — new canonical UI routes and profile-scoped resume GET.

### Frontend

- `work_hunter/web/static/ui-core.js` — canonical route parser, history synchronization, `ResourceState` constructors, and shared namespace.
- `work_hunter/web/static/ui-feedback.js` — notification center, overlay arbitration, and live-action descriptor validation.
- `work_hunter/web/static/ui-onboarding.js` — readiness, persistent/session progress, wizard transitions, and four coach marks.
- `work_hunter/web/static/ui-today.js` — deterministic Today composition and rendering with injected clock/timezone.
- `work_hunter/web/static/app.js` — existing domain/API actions, route loaders, destination renderers, and adapters into the new UI units.
- `work_hunter/web/static/index.html` — eight-destination shell, Today markup, retained workflow surfaces, overlay/toast roots, and ordered script loading.
- `work_hunter/web/static/app.css` — design tokens, Apple HIG shell, components, overlays, onboarding, responsive behavior, and reduced motion.

### Tests and docs

- `tests/test_config.py` — legacy migration and new-install defaults.
- `tests/test_web_ui_contract.py` — routes, resume API, static DOM/assets, action IDs, safety contracts.
- `tests/test_web_ui_browser.py` — canonical history, overlays, onboarding, Today, destination workflows, accessibility, responsive parity.
- `tests/test_release_artifact.py` — complete static asset list in wheel/sdist/install smoke.
- `README.md` and `CHANGELOG.md` — new navigation, onboarding reset, and safety behavior.

---

### Task 1: Add deterministic onboarding config migration

**Files:**
- Modify: `work_hunter/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `CURRENT_UI_ONBOARDING_VERSION: int = 2`
- Produces: `_migrate_ui_config_document(raw: dict[str, Any]) -> tuple[dict[str, Any], bool]`
- Changes: `default_config()["ui"]["onboarding_version"] == 0`
- Changes: `load_config(path)` persists migrated legacy documents before returning the merged config.

- [ ] **Step 1: Write failing new-install and legacy migration tests**

```python
def test_default_config_marks_new_install_for_onboarding():
    assert default_config()["ui"]["onboarding_version"] == 0


def test_load_config_marks_legacy_install_complete_and_normalizes_flat_profile(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({
            "profile": {"desired_roles": ["python"], "queries": ["python"]},
            "profiles": {"default": {"desired_roles": ["go"]}},
            "ui": {"host": "127.0.0.1", "port": 8787},
        }),
        encoding="utf-8",
    )

    loaded = load_config(path)

    assert loaded["ui"]["onboarding_version"] == 2
    assert loaded["profile"] == "legacy"
    assert loaded["profiles"]["legacy"]["desired_roles"] == ["python"]
    assert loaded["profiles"]["default"]["desired_roles"] == ["go"]
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["profile"] == "legacy"
    assert persisted["ui"]["onboarding_version"] == 2
```

Add focused cases for absent `profiles.default`, structurally equal `profiles.default`, and occupied `legacy`/`legacy-2` IDs.

- [ ] **Step 2: Run the focused tests and verify failure**

Run: `python -m pytest tests/test_config.py -k "onboarding or legacy_install" -q`

Expected: FAIL because the onboarding default and migration helper do not exist.

- [ ] **Step 3: Implement atomic migration before default merge**

```python
CURRENT_UI_ONBOARDING_VERSION = 2


def _migrate_ui_config_document(raw: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    migrated = copy.deepcopy(raw)
    changed = False
    profiles = migrated.get("profiles")
    if not isinstance(profiles, dict):
        profiles = {}
        migrated["profiles"] = profiles
        changed = True

    flat_profile = migrated.get("profile")
    if isinstance(flat_profile, dict):
        if "default" not in profiles or profiles.get("default") == flat_profile:
            target = "default"
        else:
            suffix = 1
            target = "legacy"
            while target in profiles:
                suffix += 1
                target = f"legacy-{suffix}"
        profiles[target] = copy.deepcopy(flat_profile)
        migrated["profile"] = target
        changed = True

    ui = migrated.get("ui")
    if not isinstance(ui, dict):
        ui = {}
        migrated["ui"] = ui
        changed = True
    if "onboarding_version" not in ui:
        ui["onboarding_version"] = CURRENT_UI_ONBOARDING_VERSION
        changed = True
    return migrated, changed
```

Update `default_config()` with `"onboarding_version": 0`. In `load_config`, parse the raw document, call the helper, deep-merge defaults, and call `save_config(path, merged)` only when `changed` is true.

- [ ] **Step 4: Run config regression tests**

Run: `python -m pytest tests/test_config.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the backend migration**

```powershell
git add -- work_hunter/config.py tests/test_config.py
git commit -m "feat: add UI onboarding config migration"
```

---

### Task 2: Add profile-scoped resume reads

**Files:**
- Modify: `work_hunter/web/server.py`
- Test: `tests/test_web_ui_contract.py`

**Interfaces:**
- Consumes: normalized `config["profiles"]` from Task 1.
- Produces: `GET /api/resumes?profile_id=<known-id>` returning only that profile's resumes.
- Preserves: bare `GET /api/resumes` continues using `profile_id="default"`.

- [ ] **Step 1: Write failing endpoint tests**

```python
@contextmanager
def running_ui_server(root):
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(root))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _get_json(base, path):
    try:
        with urllib.request.urlopen(f"{base}{path}", timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def test_resume_api_filters_by_valid_profile(tmp_path):
    app = WorkHunter(tmp_path)
    app.config["profiles"]["backend"] = copy.deepcopy(app.config["profiles"]["default"])
    app.save_config(app.config)
    app.storage.save_resume(Resume(name="Default", body="A", profile_id="default"))
    app.storage.save_resume(Resume(name="Backend", body="B", profile_id="backend"))
    with running_ui_server(tmp_path) as base:
        status, payload = _get_json(base, "/api/resumes?profile_id=backend")
    assert status == 200
    assert [item["name"] for item in payload] == ["Backend"]


def test_resume_api_rejects_unknown_profile(tmp_path):
    with running_ui_server(tmp_path) as base:
        status, payload = _get_json(base, "/api/resumes?profile_id=missing")
    assert status == 400
    assert payload == {"error": "invalid_profile"}
```

Add `copy` and `contextmanager` imports to the test module; reuse its existing `json`, `threading`, `urllib`, `ThreadingHTTPServer`, and `make_handler` imports.

- [ ] **Step 2: Run tests and verify failure**

Run: `python -m pytest tests/test_web_ui_contract.py -k "resume_api_filters or resume_api_rejects_unknown_profile" -q`

Expected: FAIL because the query is ignored.

- [ ] **Step 3: Implement the non-breaking query branch**

```python
if path == "/api/resumes":
    app = WorkHunter(root)
    query = parse_qs(parsed.query)
    profile_id = _str_arg(query, "profile_id") or "default"
    profiles = app.config.get("profiles") or {}
    if profile_id not in profiles:
        self._send_json({"error": "invalid_profile"}, HTTPStatus.BAD_REQUEST)
        return
    resumes = app.storage.list_resumes(profile_id=profile_id)
    self._send_json([resume.to_dict() for resume in resumes])
    return
```

- [ ] **Step 4: Run resume and web contract tests**

Run: `python -m pytest tests/test_web_ui_contract.py -k "resume" -q`

Expected: PASS.

- [ ] **Step 5: Commit the resume boundary**

```powershell
git add -- work_hunter/web/server.py tests/test_web_ui_contract.py
git commit -m "feat: scope resume API by profile"
```

---

### Task 3: Build canonical routing and static asset boundaries

**Files:**
- Create: `work_hunter/web/static/ui-core.js`
- Modify: `work_hunter/web/server.py`
- Modify: `work_hunter/web/static/index.html`
- Modify: `tests/test_web_ui_contract.py`
- Modify: `tests/test_web_ui_browser.py`
- Modify: `tests/test_release_artifact.py`

**Interfaces:**
- Produces: `window.WorkHunterUI.route.resolve(pathname, search, hash, sourceKeys)`.
- Produces: `window.WorkHunterUI.route.activate(resolved, {historyMode})`.
- Produces: `WorkHunterUI.resources.loading()`, `.ready(data, options)`, `.error(error, options)`.
- Produces: canonical `{destination, path, query, hash, canonicalUrl, warnings}` objects.

- [ ] **Step 1: Write failing route/static tests**

Add canonical routes to `UI_ROUTES` in the contract fixture and assert every new asset is served. Add a browser table test:

```python
@pytest.mark.parametrize(
    ("incoming", "canonical"),
    [
        ("/", "/today"),
        ("/favorites?filter=all", "/jobs?filter=saved"),
        ("/agent", "/applications?tab=agent"),
        ("/stats", "/analytics?tab=overview"),
        ("/trends", "/analytics?tab=trends"),
        ("/jobs?filter=all&min_score=070", "/jobs?filter=all&min_score=70"),
    ],
)
def test_router_canonicalizes_legacy_paths(browser_app, incoming, canonical):
    page, base_url, _ = browser_app
    page.goto(base_url + incoming, wait_until="domcontentloaded")
    expect(page).to_have_url(base_url + canonical)
```

Add duplicate-key, invalid-first-key, unknown-query preservation, hash preservation, `popstate`, and two-phase source-key cases from the spec.

- [ ] **Step 2: Run route/static tests and verify failure**

Run: `python -m pytest tests/test_web_ui_contract.py tests/test_web_ui_browser.py -k "route or deep_links or static_assets" -q`

Expected: FAIL because canonical routes and `ui-core.js` do not exist.

- [ ] **Step 3: Implement the shared namespace and route resolver**

```javascript
(function bootstrapCore(global) {
  const UI = global.WorkHunterUI = global.WorkHunterUI || {};
  const DESTINATIONS = Object.freeze({
    today: "/today",
    vacancies: "/jobs",
    applications: "/applications",
    calendar: "/calendar",
    assistant: "/assistant",
    analytics: "/analytics",
    sources: "/sources",
    settings: "/settings",
  });

  function canonicalInteger(value, min, max = Number.MAX_SAFE_INTEGER) {
    if (!/^(0|[1-9]\d*)$/.test(value)) return null;
    const parsed = Number(value);
    return Number.isSafeInteger(parsed) && parsed >= min && parsed <= max
      ? String(parsed) : null;
  }

  function resolve(pathname, search = "", hash = "", sourceKeys = null) {
    const params = new URLSearchParams(search);
    const warnings = [];
    const first = (key) => params.has(key) ? params.getAll(key)[0] : null;
    const forced = {
      "/": ["today", "/today", {}],
      "/favorites": ["vacancies", "/jobs", { filter: "saved" }],
      "/agent": ["applications", "/applications", { tab: "agent" }],
      "/stats": ["analytics", "/analytics", { tab: "overview" }],
      "/trends": ["analytics", "/analytics", { tab: "trends" }],
      "/chat": ["assistant", "/assistant", {}],
    };
    const canonical = {
      "/today": ["today", "/today", {}],
      "/jobs": ["vacancies", "/jobs", {}],
      "/applications": ["applications", "/applications", {}],
      "/calendar": ["calendar", "/calendar", {}],
      "/assistant": ["assistant", "/assistant", {}],
      "/analytics": ["analytics", "/analytics", {}],
      "/sources": ["sources", "/sources", {}],
      "/settings": ["settings", "/settings", {}],
    };
    const [destination, path, overrides] = forced[pathname] || canonical[pathname]
      || ["today", "/today", {}];
    const recognizedByDestination = {
      vacancies: ["filter", "source", "min_score", "status", "job"],
      applications: ["tab", "approval", "run", "operation"],
      calendar: ["event"], assistant: ["job"], analytics: ["tab"],
      settings: ["section"], today: [], sources: [],
    };
    const recognized = new Set(recognizedByDestination[destination]);
    const output = new URLSearchParams();
    const enumValue = (key, allowed, fallback = null) => {
      const value = Object.hasOwn(overrides, key) ? overrides[key] : first(key);
      if (value === null) return fallback;
      if (allowed.includes(value)) return value;
      warnings.push(`invalid_${key}`);
      return fallback;
    };
    const integerValue = (key, min, max) => {
      const raw = first(key);
      if (raw === null) return null;
      const value = canonicalInteger(raw, min, max);
      if (value === null) warnings.push(`invalid_${key}`);
      return value;
    };
    if (destination === "vacancies") {
      output.set("filter", enumValue("filter", ["all", "saved"], "all"));
      const source = first("source");
      if (source && (sourceKeys === null || sourceKeys.includes(source))) output.set("source", source);
      else if (source) warnings.push("invalid_source");
      const minScore = integerValue("min_score", 0, 100);
      if (minScore !== null) output.set("min_score", minScore);
      const status = enumValue("status", ["new", "saved", "hidden", "applied", "ghosted"]);
      if (status !== null) output.set("status", status);
      const job = integerValue("job", 1, Number.MAX_SAFE_INTEGER);
      if (job !== null) output.set("job", job);
    } else if (destination === "applications") {
      output.set("tab", enumValue("tab", ["pipeline", "agent", "automation"], "pipeline"));
      for (const key of ["approval", "run", "operation"]) {
        const value = integerValue(key, 1, Number.MAX_SAFE_INTEGER);
        if (value !== null) output.set(key, value);
      }
    } else if (destination === "calendar" || destination === "assistant") {
      const key = destination === "calendar" ? "event" : "job";
      const value = integerValue(key, 1, Number.MAX_SAFE_INTEGER);
      if (value !== null) output.set(key, value);
    } else if (destination === "analytics") {
      output.set("tab", enumValue("tab", ["overview", "trends"], "overview"));
    } else if (destination === "settings") {
      output.set("section", enumValue("section", [
        "profile", "resumes", "search", "hh", "ai", "notifications",
        "appearance", "help", "advanced",
      ], "profile"));
    }
    for (const [key, value] of params.entries()) {
      if (!recognized.has(key)) output.append(key, value);
    }
    const query = output.toString();
    const canonicalUrl = `${path}${query ? `?${query}` : ""}${hash}`;
    return { destination, path, query, hash, canonicalUrl, warnings };
  }

  function activate(resolved, { historyMode = "replace" } = {}) {
    const method = historyMode === "push" ? "pushState" : "replaceState";
    if (`${location.pathname}${location.search}${location.hash}` !== resolved.canonicalUrl) {
      history[method]({ destination: resolved.destination }, "", resolved.canonicalUrl);
    }
    global.dispatchEvent(new CustomEvent("work-hunter:route", { detail: resolved }));
    return resolved;
  }

  UI.route = Object.freeze({ DESTINATIONS, canonicalInteger, resolve, activate });
  UI.resources = Object.freeze({
    loading: () => ({ status: "loading", stale: false }),
    ready: (data, { updatedAt = null, stale = false } = {}) =>
      ({ status: "ready", data, updatedAt, stale }),
    error: (error, { data, stale = Boolean(data) } = {}) =>
      ({ status: "error", error, ...(data === undefined ? {} : { data }), stale }),
  });
})(window);
```

- [ ] **Step 4: Serve new routes and load scripts in stable order**

Add `/today`, `/applications`, `/assistant`, and `/analytics` to `work_hunter.web.server.UI_ROUTES`. At the bottom of `index.html`, load:

```html
<script src="/ui-core.js"></script>
<script src="/app.js"></script>
```

Extend `STATIC_FILES` in `tests/test_release_artifact.py` with `ui-core.js`.

- [ ] **Step 5: Run focused route and packaging tests**

Run: `python -m pytest tests/test_web_ui_contract.py tests/test_web_ui_browser.py tests/test_release_artifact.py -k "route or deep_links or static or resource" -q`

Expected: PASS.

- [ ] **Step 6: Commit routing foundation**

```powershell
git add -- work_hunter/web/server.py work_hunter/web/static/ui-core.js work_hunter/web/static/index.html tests/test_web_ui_contract.py tests/test_web_ui_browser.py tests/test_release_artifact.py
git commit -m "feat: add canonical UI routing foundation"
```

---

### Task 4: Add notification and overlay arbitration

**Files:**
- Create: `work_hunter/web/static/ui-feedback.js`
- Modify: `work_hunter/web/static/index.html`
- Modify: `work_hunter/web/static/app.css`
- Modify: `tests/test_web_ui_contract.py`
- Modify: `tests/test_web_ui_browser.py`
- Modify: `tests/test_release_artifact.py`

**Interfaces:**
- Produces: `WorkHunterUI.feedback.createNotificationCenter(root)`.
- Produces: `WorkHunterUI.feedback.createOverlayManager({sheetRoot, popoverRoot})`.
- Produces: `notifications.push({type, scope, code, title, message, action})`.
- Produces: `overlays.request({kind, trigger, render}) -> {accepted, reason}`.
- Private notification helpers defined in this task: `notificationKey`, `renderNotificationStack`, `scheduleNotificationExpiry`, `pauseNotificationTimer`, `resumeNotificationTimer`, `enforceVisibleLimit`, `dismiss`, and `resolve`.
- Private overlay helpers defined in this task: `openBlocking`, `openChildPopover`, `openBase`, `closeBase`, `closeTop`, `restoreTriggerFocus`, and `setBaseInert`.

- [ ] **Step 1: Write failing DOM and browser behavior tests**

```python
def test_feedback_roots_and_live_regions_are_local_and_accessible():
    html = _read_static("index.html")
    assert 'id="toast-region"' in html
    assert 'aria-live="polite"' in html
    assert 'id="sheet-root"' in html
    assert 'id="popover-root"' in html


def test_error_toast_persists_and_deduplicates(browser_app):
    page, base_url, _ = browser_app
    page.goto(base_url + "/today", wait_until="domcontentloaded")
    page.evaluate("""
      window.appNotifications.push({type:'error',scope:'sync',code:'offline',title:'Ошибка'});
      window.appNotifications.push({type:'error',scope:'sync',code:'offline',title:'Ошибка'});
    """)
    expect(page.locator("[data-toast-key='error:sync:offline']")).to_have_count(1)
    expect(page.locator("[data-toast-key='error:sync:offline']")).to_contain_text("2")
```

Add tests for five/eight-second timers with Playwright clock, hover/focus pause, four-toast limit, Escape LIFO, focus restoration, mobile-menu/sheet exclusion, and child-popover closure.

- [ ] **Step 2: Run focused tests and verify failure**

Run: `python -m pytest tests/test_web_ui_contract.py tests/test_web_ui_browser.py -k "toast or overlay or live_region" -q`

Expected: FAIL because roots and controllers do not exist.

- [ ] **Step 3: Implement notification center**

```javascript
function createNotificationCenter(root) {
  const entries = new Map();
  function push(input) {
    const key = `${input.type}:${input.scope}:${input.code}`;
    const existing = entries.get(key);
    if (existing && Date.now() - existing.lastAt <= 2000) {
      existing.count += 1;
      existing.lastAt = Date.now();
      render();
      return key;
    }
    entries.set(key, {
      ...input, key, count: 1, lastAt: Date.now(),
      persistent: input.type === "error" || Boolean(input.action),
      timeoutMs: input.type === "warning" ? 8000 : 5000,
    });
    enforceVisibleLimit(entries, 4);
    render();
    schedule(key);
    return key;
  }
  return Object.freeze({ push, dismiss, resolve, list: () => [...entries.values()] });
}
```

Implement `aria-live="polite"` for success/info and a separate assertive child for blocking failures without moving focus.

- [ ] **Step 4: Implement overlay manager arbitration**

```javascript
function createOverlayManager({ sheetRoot, popoverRoot }) {
  const state = { blocking: null, childPopover: null, base: null };
  function request(descriptor) {
    if (descriptor.kind === "mobileMenu" || descriptor.kind === "sheet") {
      if (state.blocking) return { accepted: false, reason: "blocking_occupied" };
      closeBase({ snoozeCoach: true });
      return openBlocking(descriptor);
    }
    if (state.blocking?.kind === "mobileMenu") {
      return { accepted: false, reason: "mobile_menu_open" };
    }
    return descriptor.kind === "childPopover"
      ? openChildPopover(descriptor)
      : openBase(descriptor);
  }
  return Object.freeze({ request, closeTop, snapshot: () => ({ ...state }) });
}
```

Use `dialog` semantics, inert base content, initial focus, Escape LIFO, outside-click rules, and trigger focus restoration exactly as specified.

- [ ] **Step 5: Load the asset and replace browser alerts**

Load `ui-feedback.js` between `ui-core.js` and `app.js`. Instantiate `window.appNotifications` and `window.appOverlays` during boot. Change `reportDynamicActionError` and existing `alert(...)` error paths to typed notifications while preserving safe text rendering.

- [ ] **Step 6: Run overlay, security, and packaging tests**

Run: `python -m pytest tests/test_web_ui_contract.py tests/test_web_ui_browser.py tests/test_web_security.py tests/test_release_artifact.py -k "toast or overlay or alert or static or security" -q`

Expected: PASS.

- [ ] **Step 7: Commit feedback foundation**

```powershell
git add -- work_hunter/web/static/ui-feedback.js work_hunter/web/static/index.html work_hunter/web/static/app.css work_hunter/web/static/app.js tests/test_web_ui_contract.py tests/test_web_ui_browser.py tests/test_release_artifact.py
git commit -m "feat: add accessible feedback and overlays"
```

---

### Task 5: Enforce typed live-action safety sheets

**Files:**
- Modify: `work_hunter/web/static/ui-feedback.js`
- Modify: `work_hunter/web/static/app.js`
- Modify: `work_hunter/web/static/index.html`
- Test: `tests/test_web_ui_contract.py`
- Test: `tests/test_web_ui_browser.py`
- Test: `tests/test_web_security.py`

**Interfaces:**
- Produces: `WorkHunterUI.feedback.validateLiveDescriptor(descriptor)`.
- Produces: `WorkHunterUI.feedback.openLiveAction(descriptor)`.
- Produces: `LiveValidationResult` status precedence: error → auth_required → capability_lost → blocked → changed → executable.
- Produces: `stableActionFingerprint(value) -> string`, `maskForUi(value) -> string`, and read-only/dry validation adapters `validateApplyMutation`, `validateReplyMutation`, `validateCampaignMutation`, `validateCleanupMutation`, `validateResumeAccountMutation`, and `validateLabMutation`.

- [ ] **Step 1: Write failing descriptor and request-safety tests**

```python
@pytest.mark.parametrize("operation_type", [
    "apply", "reply", "campaign", "cleanup", "resume_account", "api_lab",
])
def test_live_action_descriptor_requires_operation_rows(browser_app, operation_type):
    page, base_url, _ = browser_app
    page.goto(base_url + "/today", wait_until="domcontentloaded")
    result = page.evaluate(
        "type => WorkHunterUI.feedback.validateLiveDescriptor({operationType:type,targetRows:[]})",
        operation_type,
    )
    assert result == {"valid": False, "code": "missing_required_rows"}
```

Add browser route interception proving Cancel sends neither validation nor mutation, changed descriptor clears acknowledgement, missing changed replacement blocks, auth/capability loss never calls execute, and only an executable matching fingerprint sends literal `confirm: true`.

- [ ] **Step 2: Run tests and verify failure**

Run: `python -m pytest tests/test_web_ui_contract.py tests/test_web_ui_browser.py tests/test_web_security.py -k "live_action or confirmation or literal" -q`

Expected: FAIL because the registry and sheet do not exist.

- [ ] **Step 3: Implement row schemas and status validation**

```javascript
const LIVE_ROW_KEYS = Object.freeze({
  apply: ["vacancy", "company", "resume", "letter"],
  reply: ["negotiation", "employer", "recipient", "message"],
  campaign: ["run", "count", "filters", "resume"],
  cleanup: ["object_type", "count", "criteria"],
  resume_account: ["resume_or_account", "changes"],
  api_lab: ["method", "path", "params", "body"],
});

function validateLiveDescriptor(descriptor) {
  const required = LIVE_ROW_KEYS[descriptor?.operationType];
  if (!required) return { valid: false, code: "unknown_operation" };
  const keys = (descriptor.targetRows || []).map((row) => row.key);
  if (new Set(keys).size !== keys.length || required.some((key) => !keys.includes(key))) {
    return { valid: false, code: "missing_required_rows" };
  }
  if (typeof descriptor.revalidate !== "function" || typeof descriptor.execute !== "function") {
    return { valid: false, code: "missing_action_boundary" };
  }
  return { valid: true, code: "ok" };
}
```

- [ ] **Step 4: Adapt every current real mutation**

Create descriptor builders in `app.js` for HH apply, reply confirm, campaign confirm, cleanup, resume/account update/publish, and mutating API Lab methods. Each builder supplies safe rows, a fingerprint, dry/read-only `revalidate`, and `execute(confirm)` that serializes the literal boolean.

```javascript
function buildLabMutationDescriptor({ method, path, params, body }) {
  return {
    operationType: "api_lab",
    title: "Выполнить запрос к HH?",
    consequence: `${method} ${path} изменит данные аккаунта`,
    targetRows: [
      { key: "method", label: "Метод", safeValue: method },
      { key: "path", label: "Путь", safeValue: path },
      { key: "params", label: "Параметры", safeValue: maskForUi(params) },
      { key: "body", label: "Тело", safeValue: maskForUi(body) },
    ],
    fingerprint: stableActionFingerprint({ method, path, params, body }),
    revalidate: () => validateLabMutation({ method, path, params, body }),
    execute: (confirm) => api("/api/hh/lab/call", {
      method: "POST", body: JSON.stringify({ method, path, params, body, confirm }),
    }),
  };
}
```

- [ ] **Step 5: Run all HH/UI safety tests**

Run: `python -m pytest tests/test_web_security.py tests/test_web_ui_contract.py tests/test_web_ui_browser.py tests/test_hh_agent_approval.py tests/test_hh_agent_policy.py -q`

Expected: PASS.

- [ ] **Step 6: Commit safety sheets**

```powershell
git add -- work_hunter/web/static/ui-feedback.js work_hunter/web/static/app.js work_hunter/web/static/index.html tests/test_web_ui_contract.py tests/test_web_ui_browser.py tests/test_web_security.py
git commit -m "feat: add typed live-action safety sheets"
```

---

### Task 6: Implement the three-step onboarding controller and UI

**Files:**
- Create: `work_hunter/web/static/ui-onboarding.js`
- Modify: `work_hunter/web/static/index.html`
- Modify: `work_hunter/web/static/app.css`
- Modify: `work_hunter/web/static/app.js`
- Test: `tests/test_web_ui_contract.py`
- Test: `tests/test_web_ui_browser.py`
- Modify: `tests/test_release_artifact.py`

**Interfaces:**
- Produces: `WorkHunterUI.onboarding.deriveReadiness(resources)`.
- Produces: `WorkHunterUI.onboarding.createController(dependencies)`.
- Consumes: `api`, profile/source/resume `ResourceState` values, notification center, overlay manager.
- Persists exact local/session keys and schemas from the spec.

- [ ] **Step 1: Write failing first-run/readiness/persistence tests**

```python
def test_genuine_new_install_starts_at_goal_even_with_prefilled_defaults(browser_app):
    page, base_url, _ = browser_app
    page.goto(base_url + "/today", wait_until="networkidle")
    expect(page.locator("[data-onboarding-step='goal']")).to_be_visible()
    expect(page.get_by_text("Шаг 1 из 3")).to_be_visible()


def test_failed_readiness_request_never_opens_wizard(browser_app):
    page, base_url, _ = browser_app
    page.route("**/api/resumes?profile_id=*", lambda route: route.fulfill(status=500, body='{"error":"failed"}'))
    page.goto(base_url + "/today", wait_until="networkidle")
    expect(page.locator("[data-readiness-state='unknown']")).to_be_visible()
    expect(page.locator("[data-onboarding-step]")).to_have_count(0)
```

Add tests for legacy version 2 skip, Set up later/session reload, forced review/Finish review, storage-event sync, malformed storage, Step 1 exact profile mappings, source enabled-vs-health, masked HH secret preservation/clear, and one request under double click.

- [ ] **Step 2: Write failing resume partial-success and finalization tests**

Intercept create → success and activate → failure. Assert `pendingResumeActivationId` is stored, reload retries activation only, and no second create occurs. Add sync success/version-save failure and sync failure/Continue-to-Today tests asserting exact `finalization` transitions.

- [ ] **Step 3: Run onboarding tests and verify failure**

Run: `python -m pytest tests/test_web_ui_browser.py tests/test_web_ui_contract.py -k "onboarding or readiness or first_run or resume_activation" -q`

Expected: FAIL because the controller and wizard do not exist.

- [ ] **Step 4: Implement progress parsing and readiness**

```javascript
const PERMANENT_KEY = "work-hunter:onboarding:v2";
const SESSION_KEY = "work-hunter:guidance-session:v2";

function deriveReadiness({ profile, config, resumes }) {
  if ([profile, config, resumes].some((item) => item.status === "loading")) return "loading";
  if ([profile, config, resumes].some((item) => item.status === "error")) return "unknown";
  const data = profile.data?.data || {};
  const hasGoal = [...(data.desired_roles || []), ...(data.queries || [])]
    .some((value) => String(value).trim());
  const hasSource = Object.values(config.data.sources || {})
    .some((source) => source?.enabled === true);
  const activeId = profile.data.active;
  const hasResume = resumes.data.some(
    (resume) => resume.profile_id === activeId && resume.is_active === true,
  );
  return hasGoal && hasSource && hasResume ? "ready" : "incomplete";
}
```

Validate every storage field and reset malformed/wrong-version data without changing server configuration.

- [ ] **Step 5: Implement the controller state machine**

```javascript
function nextOnboardingState({ readiness, serverVersion, progress, session }) {
  if (readiness === "loading" || readiness === "unknown") return { kind: readiness };
  if (session.onboardingDeferred) return { kind: "today", guidance: readiness === "ready" };
  if (session.forceReview || serverVersion < 2) {
    const step = ["goal", "sources", "resume"]
      .find((id) => !progress.completedSteps.includes(id));
    return step ? { kind: "step", step } : { kind: "summary", forced: session.forceReview };
  }
  if (readiness === "incomplete") return { kind: "missing-domain-step" };
  return { kind: "today", guidance: true };
}
```

Implement request locks and exact goal/source/resume mappings. Persist `pendingResumeActivationId` before activation and reconcile it on reload. Persist sync outcome before version save.

- [ ] **Step 6: Render the Apple-style wizard sheet**

Render code-native fields inside the blocking sheet: Goal, Sources/optional HH, Resume, Summary. Use `data-onboarding-step`, stable control IDs, `aria-describedby`, inline errors, progress text, Cancel/Set up later, and busy labels. Load `ui-onboarding.js` after `ui-feedback.js` and before `app.js`.

- [ ] **Step 7: Run onboarding, config, security, and packaging tests**

Run: `python -m pytest tests/test_config.py tests/test_web_ui_contract.py tests/test_web_ui_browser.py tests/test_web_security.py tests/test_release_artifact.py -k "onboarding or readiness or resume or config or static or secret" -q`

Expected: PASS.

- [ ] **Step 8: Commit onboarding**

```powershell
git add -- work_hunter/web/static/ui-onboarding.js work_hunter/web/static/index.html work_hunter/web/static/app.css work_hunter/web/static/app.js tests/test_web_ui_contract.py tests/test_web_ui_browser.py tests/test_release_artifact.py
git commit -m "feat: add guided first-run onboarding"
```

---

### Task 7: Implement deterministic Today composition

**Files:**
- Create: `work_hunter/web/static/ui-today.js`
- Modify: `work_hunter/web/static/index.html`
- Modify: `work_hunter/web/static/app.css`
- Modify: `work_hunter/web/static/app.js`
- Test: `tests/test_web_ui_browser.py`
- Modify: `tests/test_release_artifact.py`

**Interfaces:**
- Produces: `WorkHunterUI.today.compose({resources, now, timeZone})`.
- Produces: `{readiness, focus, freshMatches, upcoming, pendingDecision}`.
- Consumes: injected `now`, IANA `timeZone`, and `ResourceState` inputs.
- Private helpers defined in this task: `createZonedClock`, `parseZonedValue`, `selectFocus`, `selectFreshMatches`, `selectUpcoming`, and `selectPendingDecision`.

- [ ] **Step 1: Write failing deterministic composer tests**

Use `page.evaluate` against `WorkHunterUI.today.compose` with fixed `now` and `timeZone="Europe/Moscow"`. Cover pending approvals, open tasks, today events, 30-day overdue exclusion, category/tie keys, finite scores, invalid published-to-valid-fetched fallback, invalid dates, date-only parsing, and a DST timezone such as `Europe/Berlin`.

```python
def test_today_fresh_matches_exclude_unscored_and_fall_back_to_fetched(browser_app):
    page, base_url, _ = browser_app
    page.goto(base_url + "/today", wait_until="domcontentloaded")
    args = {
        "now": "2026-07-13T09:00:00Z",
        "timeZone": "Europe/Moscow",
        "resources": {
            "readiness": "ready",
            "jobs": {"status": "ready", "stale": False, "data": [
                {"id": 1, "status": "new", "score": {"total_score": 80}, "published_at": "2026-07-11T09:00:00Z", "fetched_at": "2026-07-13T08:00:00Z"},
                {"id": 2, "status": "new", "score": {"total_score": 90}, "published_at": "invalid", "fetched_at": "2026-07-13T08:00:00Z"},
                {"id": 3, "status": "new", "score": {"total_score": 85}, "published_at": "2026-07-12T09:00:00Z", "fetched_at": "2026-07-12T09:00:00Z"},
                {"id": 4, "status": "new", "score": None, "published_at": "2026-07-13T09:00:00Z", "fetched_at": "2026-07-13T09:00:00Z"},
            ]},
            "tasks": {"status": "ready", "stale": False, "data": []},
            "events": {"status": "ready", "stale": False, "data": []},
            "approvals": {"status": "ready", "stale": False, "data": []},
            "sources": {"status": "ready", "stale": False, "data": []},
        },
    }
    result = page.evaluate(
        "args => WorkHunterUI.today.compose(args).freshMatches.map(item => item.id)",
        args,
    )
    assert result == [2, 3, 1]
```

- [ ] **Step 2: Run composer tests and verify failure**

Run: `python -m pytest tests/test_web_ui_browser.py -k "today_" -q`

Expected: FAIL because `ui-today.js` does not exist.

- [ ] **Step 3: Implement clock-safe composition**

```javascript
function compose({ resources, now, timeZone }) {
  const clock = createZonedClock(now, timeZone);
  const focus = selectFocus(resources, clock).slice(0, 3);
  const freshMatches = selectFreshMatches(resources.jobs).slice(0, 3);
  const upcoming = selectUpcoming(resources.events, clock).slice(0, 2);
  return { readiness: resources.readiness, focus, freshMatches, upcoming,
    pendingDecision: selectPendingDecision(resources.approvals) };
}
```

Implement Temporal-compatible overlap/gap parsing without adding a runtime dependency: use `Intl.DateTimeFormat(...).formatToParts` and a bounded offset search, choosing the earlier matching instant or shifting forward through a gap.

- [ ] **Step 4: Render Today from real loaded resources**

Add `#view-today` with readiness strip, Focus, Fresh matches, Upcoming, and pending decision regions. In `app.js`, load each resource independently with `Promise.allSettled`, pass `new Date().toISOString()` plus `Intl.DateTimeFormat().resolvedOptions().timeZone`, and render section-level loading/error/empty states.

- [ ] **Step 5: Verify Today browser behavior**

Run: `python -m pytest tests/test_web_ui_browser.py -k "today or initial_source_failure or loading" -q`

Expected: PASS.

- [ ] **Step 6: Commit Today**

```powershell
git add -- work_hunter/web/static/ui-today.js work_hunter/web/static/index.html work_hunter/web/static/app.css work_hunter/web/static/app.js tests/test_web_ui_browser.py tests/test_release_artifact.py
git commit -m "feat: add deterministic Today dashboard"
```

---

### Task 8: Rebuild the shell and Vacancies in Apple HIG style

**Files:**
- Modify: `work_hunter/web/static/index.html`
- Modify: `work_hunter/web/static/app.css`
- Modify: `work_hunter/web/static/app.js`
- Test: `tests/test_web_ui_contract.py`
- Test: `tests/test_web_ui_browser.py`

**Interfaces:**
- Consumes: canonical router, notifications, overlays, onboarding, Today.
- Produces: eight-destination shell and `data-action-id` coverage for Today/Vacancies.
- Preserves: existing jobs, filters, bulk actions, detail actions, notes, AI preparation, and dry/live HH workflows.

- [ ] **Step 1: Write failing shell and Vacancies capability tests**

Assert exactly eight navigation destinations, Russian-first labels, system font stack, no emoji/text-glyph production icons, and no `window.alert`/`window.confirm`. Add a browser capability test that selects a job, exercises save/hide/applied, opens Prepare and More disclosure groups, and verifies all existing job action IDs.

```python
EXPECTED_VACANCY_ACTIONS = {
    "job.save", "job.hide", "job.applied", "job.note.save",
    "job.letter.local", "job.letter.ai", "job.description.fetch",
    "job.hh.plan", "job.hh.live", "job.telegram.share",
    "job.ai.classify", "job.ai.structure", "job.ai.gap",
    "job.ai.fit", "job.ai.ats-audit", "job.ai.ats-resume",
    "job.ai.summary", "job.ai.resume-tips", "job.ai.interview",
    "job.ai.experience-pitch",
}
```

- [ ] **Step 2: Run tests and verify failure**

Run: `python -m pytest tests/test_web_ui_contract.py tests/test_web_ui_browser.py -k "navigation or vacancies or job_" -q`

Expected: FAIL against the nine-item legacy sidebar and mixed action surface.

- [ ] **Step 3: Replace shell markup while preserving workflow IDs**

Use semantic `<aside>`, `<nav aria-label="Основная навигация">`, `<main>`, and one active route section. Each nav control uses a local SVG icon with `aria-hidden="true"` and a visible/accessible label. Keep existing form/table/detail IDs used by API actions; move low-frequency job actions under code-native `<details>` groups without changing their handlers.

- [ ] **Step 4: Replace visual tokens and component styles**

```css
:root {
  --bg: #f5f5f7;
  --surface: rgba(255,255,255,.86);
  --text: #1d1d1f;
  --secondary: #6e6e73;
  --separator: rgba(60,60,67,.18);
  --blue: #0a84ff;
  --green: #34c759;
  --amber: #ff9f0a;
  --red: #ff453a;
  --sidebar-width: 232px;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
```

Use open lists/tables, 8 px spacing rhythm, 8–16 px radii, neutral borders, and elevation only for overlays. Add explicit `:focus-visible`, disabled, busy, hover, active, dark-theme, and reduced-motion states.

- [ ] **Step 5: Attach stable action identifiers**

Set `data-action-id` on every static and dynamic Today/Vacancy control. Update dynamic render helpers so record IDs remain in `data-record-id` and capability IDs remain stable strings.

- [ ] **Step 6: Run job workflow and security regressions**

Run: `python -m pytest tests/test_web_ui_contract.py tests/test_web_ui_browser.py tests/test_web_security.py -k "job or vacancy or navigation or markup or handler" -q`

Expected: PASS.

- [ ] **Step 7: Commit shell and Vacancies**

```powershell
git add -- work_hunter/web/static/index.html work_hunter/web/static/app.css work_hunter/web/static/app.js tests/test_web_ui_contract.py tests/test_web_ui_browser.py
git commit -m "feat: rebuild shell and vacancy workspace"
```

---

### Task 9: Migrate Applications and the remaining destinations

**Files:**
- Modify: `work_hunter/web/static/index.html`
- Modify: `work_hunter/web/static/app.css`
- Modify: `work_hunter/web/static/app.js`
- Test: `tests/test_web_ui_contract.py`
- Test: `tests/test_web_ui_browser.py`

**Interfaces:**
- Produces: `/applications?tab=pipeline|agent|automation`.
- Produces: `/analytics?tab=overview|trends`.
- Produces: `/settings?section=<allowed-section>`.
- Preserves: every capability inventory entry and legacy-route deep link.

- [ ] **Step 1: Write failing destination capability matrix tests**

Create seeded expected action sets for Applications pipeline/agent/automation, Calendar, Assistant, Analytics, Sources, and each Settings section. Verify `/agent`, `/chat`, `/stats`, `/trends`, and `/favorites` normalize to the correct destination/subview.

```python
EXPECTED_DESTINATION_ACTIONS = {
    "applications.agent": {"agent.refresh", "agent.live-auth", "agent.approval.approve", "agent.approval.reject"},
    "applications.automation": {"agent.research.plan", "agent.research.run", "agent.template.save", "agent.blacklist.save", "agent.batch-matrix"},
    "calendar": {"event.create", "event.edit", "event.delete", "task.open"},
    "assistant": {"chat.send", "chat.attach-job", "search.ai", "search.save"},
    "analytics": {"stats.refresh", "stats.export-csv", "trends.run"},
    "sources": {"sources.sync", "source.retry"},
    "settings": {"profile.save", "resume.create", "resume.edit", "resume.activate", "config.save", "onboarding.restart", "guidance.restart"},
    "settings.advanced": {"hh.lab.run", "hh.lab.snippet.save", "hh.lab.snippet.delete"},
}
```

- [ ] **Step 2: Run matrix tests and verify failure**

Run: `python -m pytest tests/test_web_ui_contract.py tests/test_web_ui_browser.py -k "destination or applications or analytics or settings" -q`

Expected: FAIL because legacy standalone views remain.

- [ ] **Step 3: Compose Applications tabs from existing agent sections**

Move pipeline/ghost follow-up to Pipeline, approvals/digest/operations/outcomes to Agent, and templates/blacklist/research/batch matrix/negotiations to Automation. Preserve existing IDs and event handlers; add tab semantics and route synchronization.

- [ ] **Step 4: Compose remaining destinations**

Move chat to Assistant, stats/trends to Analytics tabs, source status to Sources, and group Settings into the allowed section enum. Move API Lab/snippets to Settings → Advanced. Keep each loader isolated so a failed request renders only that section's inline Retry.

- [ ] **Step 5: Add stable action IDs to every migrated surface**

For dynamic approvals, runs, events, resumes, searches, sources, and snippets, emit stable `data-action-id` plus separate record identity attributes. Ensure icon-only actions have accessible names and visible hover/focus tooltips.

- [ ] **Step 6: Run destination and existing workflow regressions**

Run: `python -m pytest tests/test_web_ui_browser.py tests/test_web_ui_contract.py -k "agent or resume or ghost or stats or trends or source or calendar or chat" -q`

Expected: PASS.

- [ ] **Step 7: Commit destination migration**

```powershell
git add -- work_hunter/web/static/index.html work_hunter/web/static/app.css work_hunter/web/static/app.js tests/test_web_ui_contract.py tests/test_web_ui_browser.py
git commit -m "feat: migrate UI destinations and workflows"
```

---

### Task 10: Add contextual guidance and responsive action parity

**Files:**
- Modify: `work_hunter/web/static/ui-onboarding.js`
- Modify: `work_hunter/web/static/ui-feedback.js`
- Modify: `work_hunter/web/static/app.css`
- Modify: `work_hunter/web/static/app.js`
- Test: `tests/test_web_ui_browser.py`

**Interfaces:**
- Produces exact coach IDs: `find-vacancies`, `match-score`, `vacancy-actions`, `live-safety`.
- Produces stable anchors: `data-guide="find-vacancies|match-score|vacancy-actions|live-hh-action"`.
- Produces equal `data-action-id` sets at desktop and mobile for seeded surfaces.
- Defines eligibility helpers `canFind`, `hasScoredJob`, `hasStatusActions`, and `hasLivePlan`, each returning a strict boolean from the current route/resources.

- [ ] **Step 1: Write failing coach-mark state tests**

Test the exact route/prerequisite/anchor table, one-new-mark-per-session, Got it, permanent dismiss, Escape snooze, Skip introduction, navigation-before-DOM-removal precedence, rerender anchor disappearance, covered-anchor deferral, and sheet/mobile-menu arbitration.

- [ ] **Step 2: Write failing responsive parity tests**

At 1440×900, 900×900, and 680×844 assert:

```python
assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
title = page.locator("#view-today h1").bounding_box()
primary = page.locator("[data-action-id='jobs.sync']").bounding_box()
assert title and title["y"] < 600
assert primary and primary["y"] < 600
```

Open each mobile row Actions menu and compare the collected `data-action-id` set to the desktop fixture for every destination from Task 9. Verify all eight menu destinations are keyboard reachable.

- [ ] **Step 3: Run tests and verify failure**

Run: `python -m pytest tests/test_web_ui_browser.py -k "coach or responsive or action_parity or viewport" -q`

Expected: FAIL because guidance and mobile transformations are incomplete.

- [ ] **Step 4: Implement the coach scheduler**

```javascript
const COACH_MARKS = Object.freeze([
  { id: "find-vacancies", route: "today", anchor: '[data-guide="find-vacancies"]', eligible: canFind },
  { id: "match-score", route: "vacancies", anchor: '[data-guide="match-score"]', eligible: hasScoredJob },
  { id: "vacancy-actions", route: "vacancies", anchor: '[data-guide="vacancy-actions"]', eligible: hasStatusActions },
  { id: "live-safety", route: "vacancies", anchor: '[data-guide="live-hh-action"]', eligible: hasLivePlan },
]);
```

Require anchors to be connected, non-inert, intersecting, and topmost at center via `elementFromPoint`. Explicit navigation snoozes before DOM removal; data rerender closure does not snooze.

- [ ] **Step 5: Implement exact responsive modes**

At 721–960 px render the 72 px icon sidebar with accessible labels/tooltips. At 720 px and below render the compact top bar and mobile-menu blocking sheet, stack Today content, and expose row actions through labeled menus without changing action IDs.

- [ ] **Step 6: Run guidance, accessibility, and viewport tests**

Run: `python -m pytest tests/test_web_ui_browser.py tests/test_web_ui_contract.py -k "coach or keyboard or focus or responsive or action_parity or reduced_motion" -q`

Expected: PASS.

- [ ] **Step 7: Commit guidance and responsive behavior**

```powershell
git add -- work_hunter/web/static/ui-onboarding.js work_hunter/web/static/ui-feedback.js work_hunter/web/static/app.css work_hunter/web/static/app.js tests/test_web_ui_browser.py tests/test_web_ui_contract.py
git commit -m "feat: add contextual guidance and responsive parity"
```

---

### Task 11: Complete documentation and release verification

**Files:**
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify: `tests/test_release_artifact.py`
- Verify: all files changed by Tasks 1–10

**Interfaces:**
- Consumes: all completed UI/backend tasks.
- Produces: verified wheel/sdist static resources and user-facing navigation/onboarding documentation.

- [ ] **Step 1: Update release resource assertions**

Ensure `STATIC_FILES` contains:

```python
STATIC_FILES = {
    "app.css", "app.js", "index.html", "manifest.json", "sw.js",
    "ui-core.js", "ui-feedback.js", "ui-onboarding.js", "ui-today.js",
}
```

Run: `python -m pytest tests/test_release_artifact.py -q`

Expected: PASS.

- [ ] **Step 2: Document the new UI**

Add README sections for the eight destinations, first-run three-step flow, **Set up later**, **Run introduction again**, **Show tips again**, and the fact that HH live actions still require explicit confirmation and server authorization. Add a dated CHANGELOG entry listing the redesign without claiming autonomous live applications.

- [ ] **Step 3: Run focused browser suite**

Run: `python -m pytest tests/test_web_ui_browser.py -q`

Expected: PASS with no page errors, unexpected console errors, or external requests.

- [ ] **Step 4: Run the full project quality gates**

```powershell
python -m pytest -q
ruff check .
mypy work_hunter
git diff --check
python -m build
python -m pip check
```

Expected: every command exits 0. If `python -m build` is unavailable in the active environment, install the declared `release` extra and rerun the same command; do not skip artifact verification.

- [ ] **Step 5: Perform browser visual QA**

Start `python -m work_hunter ui --host 127.0.0.1 --port 8787`. Verify Today, Vacancies, Applications, Calendar, Assistant, Analytics, Sources, Settings, onboarding, a help popover, success/error toasts, and a safety sheet at all three acceptance viewports. Capture screenshots, inspect them with `view_image`, and record/fix every mismatch against the spec's typography, color semantics, spacing, component anatomy, breakpoints, and state tables.

- [ ] **Step 6: Commit release evidence and docs**

```powershell
git add -- README.md CHANGELOG.md tests/test_release_artifact.py
git commit -m "docs: document Apple HIG onboarding UI"
```

---

## Self-Review Results

- **Spec coverage:** Tasks 1–2 cover config/API integration; Tasks 3–5 cover routing, feedback, overlays, and HH safety; Tasks 6–7 cover onboarding and Today; Tasks 8–10 preserve/reorganize every UI capability and responsive/accessibility behavior; Task 11 covers packaging, docs, full tests, and visual QA.
- **Completion-marker scan:** The plan contains no unfinished markers, deferred implementation, or unspecified error-handling steps. Every code-changing step names exact files, interfaces, commands, and expected results.
- **Type consistency:** `window.WorkHunterUI`, `ResourceState`, route result objects, notification keys, overlay requests, onboarding storage keys, Today composer inputs, coach IDs, `LiveActionDescriptor`, and `data-action-id` are defined once and consumed under the same names in later tasks.
- **Scope:** This is one staged UI program. Each task ends in an independently testable deliverable and a focused commit; backend/domain architecture and new external integrations remain out of scope.
