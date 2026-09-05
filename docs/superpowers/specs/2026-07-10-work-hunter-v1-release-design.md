# Work Hunter 1.0 Release Design

**Date:** 2026-07-10

**Target version:** 1.0.0

**Release type:** private, local-first release distributed as a source checkout, Python wheel, and Docker image

## Goal

Ship Work Hunter 1.0 as a safe and reproducible local application. The release must preserve the existing CLI, MCP, SQLite, and browser cockpit workflows while closing every proven P0/P1 issue found in the July 2026 audit.

The release supports real HH and external-board applications through the same plan/confirm contract. Automated verification uses fakes and dry runs; an account-bound live smoke test may be run only for a vacancy explicitly selected by the operator.

## Audit Baseline

The starting branch is `codex/mega-monster-plan` at commit `595558f`.

- `pytest -q`: 243 passed.
- `ruff check .`: passed.
- `mypy work_hunter`: 59 errors.
- `git diff --check main...HEAD`: failed on three whitespace defects.
- A wheel can be built, but it omits `work_hunter/web/static/*` and `work_hunter/migrations/*.sql`; the installed UI returns HTTP 404.
- Docker cannot be considered runnable because it installs the project with `--no-deps` without explicitly installing `requests`.
- The tested dependency set resolves to vulnerable `starlette==0.46.2` because the project pins `starlette<0.47`.
- Docker Desktop was not running during the audit, so the original image was not built.

## Selected Approach

Harden the existing architecture instead of rewriting it. `WorkHunter` remains the service facade, `Storage` remains the SQLite boundary, the standard-library HTTP server remains the local cockpit server, and the current static frontend remains the UI.

Changes are grouped into five independently testable boundaries:

1. live-action and local HTTP safety;
2. persistence and data correctness;
3. browser cockpit correctness;
4. packaging, Docker, and dependency integrity;
5. release gates and documentation.

Large-scale splitting of `services.py` and `storage.py` is deferred. Release fixes may extract small focused helpers when they eliminate duplicated safety or persistence logic, but they must not redesign unrelated workflows.

## 1. Live-Action And Local HTTP Safety

### Shared confirmation rules

- Confirmation values are accepted only when the decoded JSON value is the boolean `true`. Strings such as `"true"` and `"false"`, numbers, and non-empty containers never authorize a live action.
- A single service-level mutation guard is used by CLI, UI, and internal callers. Transport wrappers cannot weaken it.
- Blocked actions return a structured result containing `status="blocked"`, a stable code, `requires_confirmation=true`, and risk flags. They do not call an HTTP transport.

### HH API Lab

- `hh_api_lab_call()` gains `confirm: bool = False`.
- `GET`, `HEAD`, and `OPTIONS` remain read-only and need no confirmation.
- `POST`, `PUT`, `PATCH`, and `DELETE` are blocked unless `confirm is True`.
- The web UI shows a second explicit warning for a mutating Lab request and sends a literal JSON boolean only after acceptance.
- Every accepted mutation is written to the existing operation audit log with method, normalized path, masked parameters/body, confirmation state, result status, and risk flags.
- Existing absolute-URL, OAuth/token-path, and secret-masking guards remain in force.

### Resume mutations

- Resume creation and update/publish operations default to dry-run or blocked mode.
- CLI and HTTP entry points require an explicit confirmation for account mutations.
- Read-only resume listing, validation, template preview, and local draft construction remain confirmation-free.

### Browser cockpit request boundary

- The server refuses to bind to a non-loopback host. Remote exposure is outside the 1.0 contract.
- Mutating HTTP requests require `Content-Type: application/json`; form and `text/plain` bodies are rejected.
- `Host` must resolve to the configured loopback listener. Non-loopback and DNS-rebinding host values are rejected.
- When an `Origin` header exists, it must match the loopback listener origin. Cross-origin requests are rejected before reading the body.
- `OPTIONS` never authorizes cross-origin mutation access.
- Responses add `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, and `Referrer-Policy: no-referrer`.
- Existing local scripts without an `Origin` header continue to work when they use JSON and the loopback host.

### Masked configuration updates

- `***` is a display sentinel, never a value to persist for a secret field.
- A recursive server-side merge preserves the stored value whenever a recognized secret field is submitted as `***`.
- A secret changes only when the client supplies a new non-empty, non-sentinel value. Clearing uses `POST /api/config/secret/clear` with JSON `{"path": "<allowlisted-secret-path>", "confirm": true}`; the route accepts only known secret paths and literal confirmation.
- API responses, logs, audit rows, and errors remain masked.

## 2. Persistence And Data Correctness

### OAuth token rotation

- Token refresh updates the active HH account profile in `WorkHunter.config` and atomically rewrites the config file.
- The in-memory client, in-memory application config, and on-disk config receive the same access token, refresh token, and expiry values.
- A rotated refresh token must survive construction of the next client instance.

### Profile-aware scoring

- Every score is saved with the active profile ID.
- `get_job`, `list_jobs`, sorting, exports, reports, and UI reads request the active profile score instead of hard-coding `default`.
- Scores for two profiles can coexist for one job without overwriting one another.

### SQLite integrity and migrations

- Every connection enables `PRAGMA foreign_keys=ON` and a finite busy timeout before schema work.
- Schema initialization and compatibility column upgrades are serialized with an immediate SQLite transaction. Concurrent constructors must not race on `ALTER TABLE`.
- Each versioned migration and its `schema_migrations` record commit atomically; an error rolls back both schema changes and the version record.
- Orphan applications and other invalid foreign-key rows are rejected.
- Package-resource absence is an error reported by `doctor`, not a silent migration skip.

### Source identity and ghost timing

- Public-board fallback identity uses a canonical full URL, including sorted non-tracking query parameters. `utm_*`, `gclid`, `yclid`, and fragment data are removed.
- When a source provides no stable vacancy ID, the source ID is a deterministic SHA-256-derived identifier of that canonical URL.
- `get_ghost_jobs(days=N)` uses a UTC cutoff equal to current time minus `N` days. Fresh applications do not become ghosts before the requested interval.

### HH transport fallback

- `requests.RequestException` subclasses are converted to `HHTransportError` with a masked, actionable message.
- The documented web fallback is entered for connection failures and timeouts covered by the fallback policy.
- Authentication and challenge errors retain their typed outcomes and are not mislabeled as network failures.

## 3. Browser Cockpit Correctness

- Editing a resume loads the selected record into the form, preserves its active state unless the user changes it, and updates only the selected resume.
- A ghost-row action passes that row's job ID directly; it never uses a previously selected global job.
- Opening `/agent` performs only a local dry preflight. Credentialed `/me` validation runs only after the user presses the explicit live-auth check.
- Stats use one shared status mapping. Total applications and funnel stages are derived from the same data, and rendered widths are capped at 100 percent.
- Job rows are keyboard focusable and open on Enter or Space while retaining checkbox behavior.
- Favorites receive and display their note preview through a documented API field.
- Busy buttons save their label when the operation starts and restore it on success or failure, including buttons created after page load.
- Initial loading has a visible loading state and a recoverable error state; one failed request does not abort unrelated page initialization.
- All data inserted through `innerHTML`, including source and status labels, is escaped. Inline JavaScript interpolation accepts only validated numeric IDs or escaped strings.
- Runtime dependencies on Google Fonts and `unpkg.com/...@latest` are removed. The UI uses system fonts and local assets only; no executable JavaScript is fetched from the internet.

## 4. Packaging, Docker, And Dependencies

### Python package

- `pyproject.toml` defines the setuptools build backend explicitly.
- Wheel and sdist include all static UI files and SQL migrations as package data.
- Version metadata in `pyproject.toml` and `work_hunter.__init__` is `1.0.0` and is checked by a test.
- `doctor` reports the installed version, real editable/wheel mode, static-resource status, migration-resource status, and a warning when config has not been initialized.

### Dependency policy

- Runtime bounds selected for release verification are `mcp>=1.27,<2`, `requests>=2.32,<3`, and `starlette>=1.3.1,<2`.
- The Starlette floor removes the advisories found against 0.46.2.
- Dev dependencies retain pytest, Ruff, mypy, and requests stubs. A separate `release` extra contains `build` and `pip-audit`; neither becomes a runtime dependency.
- The clean release environment must pass `pip check` and report no known vulnerability in Work Hunter runtime dependencies. Vulnerabilities in installer tooling are handled by upgrading pip before the audit and are not hidden with blanket ignores.

### Docker

- Docker builds and installs the same wheel that is tested outside the image; it does not duplicate a partial dependency list or use `--no-deps` against an incomplete environment.
- The runtime image uses a non-root application user and a writable `/data` directory owned by that user.
- `.dockerignore` excludes Git data, local secrets, databases, logs, HAR/session/cookie files, keys, research repositories, browser artifacts, caches, virtual environments, build output, and test output.
- The image entry point keeps the safe `hh-auth-status` default and performs no live mutation.

## 5. Type, Test, And Release Gates

### Required automated gates

The release is accepted only when all commands exit zero from a clean checkout:

```powershell
python -m pip install --upgrade "pip>=26.1.2"
python -m pip install -e ".[dev,browser,ui,release]"
python -m playwright install chromium
python -m pytest -q
python -m pytest tests/test_web_ui_browser.py -q
ruff check .
mypy work_hunter
git diff --check
python -m build
python -m pip check
python -m pip_audit
```

The 59 baseline mypy errors are fixed at their source. Broad module-level ignores, blanket `ignore_missing_imports`, and disabling error codes are not accepted as a release fix.

### Required regression coverage

- API Lab mutation blocked without literal confirmation and accepted with literal `true` only.
- Cross-origin, non-JSON, and non-loopback-host mutations rejected before service dispatch.
- Masked config round-trip preserves every stored secret.
- Resume create/update/publish gates are safe by default.
- Rotated refresh token persists to memory and disk.
- Two profiles retain distinct scores and queries return the active one.
- Foreign keys reject orphans; concurrent storage initialization succeeds; failed migrations leave no partial schema.
- Query-based vacancy identities remain distinct.
- Ghost cutoff honors `days`.
- Requests connection failures reach the approved fallback.
- Resume edit, ghost action, dry agent load, stats mapping, keyboard rows, busy restoration, and initial error isolation have executable coverage.
- Built wheel contains static and migration resources.

The browser test starts the local server on an ephemeral loopback port, loads the UI in headless Chromium, fails on uncaught page/console errors, exercises keyboard job selection, resume edit, ghost-row targeting, dry agent load, and verifies that no request targets a non-loopback origin.

### Artifact smoke tests

The wheel is installed into a new virtual environment outside the repository. From that environment:

- `work-hunter --help` exits zero;
- `work-hunter --root <empty-temp-root> init` exits zero;
- `work-hunter --root <empty-temp-root> doctor --json` reports healthy package resources and no configured HH credentials;
- the UI root and `/app.js` return HTTP 200;
- MCP initializes and lists tools without network access;
- no real HH request is made.

The Docker image must build and run the safe default command when a Docker daemon is available. If the local daemon remains unavailable, that environmental limitation is reported explicitly and the release is not represented as Docker-verified.

## Release Documentation

- `README.md` is updated to match the actual install, doctor, safety, wheel, and Docker behavior.
- `CHANGELOG.md` records the 1.0.0 release and its safety/data/packaging fixes.
- A release checklist records the exact commands and results without secrets or machine-specific paths.
- `.github/workflows/ci.yml` runs Python 3.11 and 3.12 tests plus dedicated lint, type, build/wheel-smoke, and runtime dependency-audit jobs. It never publishes an artifact or performs a live HH request.
- Existing historical plans remain historical; their completed checkboxes are not treated as current verification evidence.

## Explicit Exclusions

- No real application, reply, form submission, resume publication, negotiation deletion, blacklist mutation, or arbitrary mutating HH API call is executed during release work.
- The existing private `.work-hunter` directory and its credentials are not read, copied, rewritten, or used for tests.
- No public package publication, GitHub release, remote deployment, push, or pull request is created without a separate user request.
- No full service/storage architectural rewrite is included in 1.0.0.

## Definition Of Done

Work Hunter 1.0 is done when every P0/P1 item in this design has a regression test, all required gates pass, a clean wheel serves the UI and applies migrations, Docker is either verified or explicitly reported as the sole environmental gap, Git is clean except for the intentional release changes, and the final report lists exact verification results. A passing editable-install pytest suite alone is not sufficient.
