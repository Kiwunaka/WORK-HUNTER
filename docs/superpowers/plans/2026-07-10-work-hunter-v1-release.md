# Work Hunter 1.0 Packaging And Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce type-clean, vulnerability-checked Work Hunter 1.0 source, wheel, and Docker artifacts with reproducible CI and accurate release documentation.

**Architecture:** Build one setuptools wheel containing Python, SQL migrations, and the static cockpit; install that wheel in Docker and artifact smoke environments; make `doctor` validate the installed resources; enforce tests, Ruff, mypy, browser, build, audit, and whitespace gates in CI.

**Tech Stack:** Python 3.11/3.12, setuptools, build, pytest, Ruff, mypy, pip-audit, Playwright, Docker, GitHub Actions.

## Global Constraints

- Target version is exactly `1.0.0` in project and module metadata.
- Runtime bounds are `mcp>=1.27,<2`, `requests>=2.32,<3`, and `starlette>=1.3.1,<2`.
- Build/release tools are optional dependencies, never runtime dependencies.
- No blanket mypy ignore, disabled error code, or `ignore_missing_imports` workaround.
- CI and smoke tests never use repository `.work-hunter` state or make a live HH mutation.
- No package publication, tag, GitHub release, push, or deployment is part of this plan.

## Cross-Plan Execution Order

1. `2026-07-10-work-hunter-v1-safety.md` establishes shared confirmation and HTTP boundaries.
2. `2026-07-10-work-hunter-v1-data.md` changes service/storage behavior after safety signatures stabilize.
3. `2026-07-10-work-hunter-v1-ui.md` consumes the final HTTP/data contracts and adds browser coverage.
4. This plan fixes remaining types, builds artifacts, adds CI/docs, and runs the complete release gate.

Coverage against the approved design is complete: safety sections map to the safety plan; OAuth/profile/SQLite/identity/ghost/fallback sections map to the data plan; every cockpit bullet maps to the UI plan; package/dependency/Docker/type/test/documentation sections map to this plan. No approved requirement is deferred except the explicitly excluded full service/storage rewrite and real HH verification.

---

### Task 1: Versioned Package Metadata And Runtime Resources

**Files:**
- Modify: `pyproject.toml`
- Modify: `.gitignore`
- Modify: `work_hunter/__init__.py`
- Create: `tests/test_release_artifact.py`

**Interfaces:**
- Produces wheel version `1.0.0`.
- Packages `work_hunter/migrations/*.sql` and `work_hunter/web/static/*`.
- Adds `release` extra with `build` and `pip-audit`.

- [ ] **Step 1: Add failing metadata and wheel-content tests**

Create `tests/test_release_artifact.py`:

```python
from __future__ import annotations

import subprocess
import sys
import tarfile
import tomllib
import zipfile
from pathlib import Path

import work_hunter


ROOT = Path(__file__).resolve().parents[1]


def test_project_and_module_versions_are_1_0_0():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["version"] == "1.0.0"
    assert work_hunter.__version__ == "1.0.0"


def test_built_wheel_contains_ui_and_migrations(tmp_path):
    output = tmp_path / "dist"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "build",
            "--wheel",
            "--outdir",
            str(output),
            str(ROOT),
        ],
        check=True,
        cwd=tmp_path,
    )
    wheel = next(output.glob("work_hunter-1.0.0-*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
    assert "work_hunter/web/static/index.html" in names
    assert "work_hunter/web/static/app.js" in names
    assert "work_hunter/web/static/app.css" in names
    assert "work_hunter/web/static/manifest.json" in names
    assert "work_hunter/web/static/sw.js" in names
    assert "work_hunter/migrations/0001_backbone.sql" in names


def test_built_sdist_contains_ui_and_migrations(tmp_path):
    output = tmp_path / "dist"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "build",
            "--sdist",
            "--outdir",
            str(output),
            str(ROOT),
        ],
        check=True,
        cwd=tmp_path,
    )
    archive_path = next(output.glob("work_hunter-1.0.0.tar.gz"))
    with tarfile.open(archive_path, "r:gz") as archive:
        names = set(archive.getnames())
    prefix = "work_hunter-1.0.0/"
    assert prefix + "work_hunter/web/static/index.html" in names
    assert prefix + "work_hunter/migrations/0001_backbone.sql" in names
```

- [ ] **Step 2: Run release-artifact tests and verify RED**

Run:

```powershell
pytest tests/test_release_artifact.py -q
```

Expected: version assertion fails and the wheel resource assertions fail.

- [ ] **Step 3: Configure the build backend, versions, resources, and safe dependency floors**

Add to the top of `pyproject.toml`:

```toml
[build-system]
requires = ["setuptools>=75,<81"]
build-backend = "setuptools.build_meta"
```

Set project metadata and dependencies:

```toml
[project]
name = "work-hunter"
version = "1.0.0"
description = "Personal local job-search command center with MCP tools and a local UI."
readme = "README.md"
requires-python = ">=3.11"
dependencies = [
  "mcp>=1.27,<2",
  "requests>=2.32,<3",
  "starlette>=1.3.1,<2",
]

[project.optional-dependencies]
dev = [
  "mypy>=1.10,<3",
  "pytest>=8,<10",
  "ruff>=0.5,<1",
  "types-requests>=2.32",
]
browser = [
  "beautifulsoup4>=4.12,<5",
  "playwright>=1.45,<2",
]
ui = ["uvicorn>=0.30,<1"]
release = [
  "build>=1.2,<2",
  "pip-audit>=2.10,<3",
]
```

Add:

```toml
[tool.setuptools.package-data]
work_hunter = [
  "migrations/*.sql",
  "web/static/*",
]
```

Set `work_hunter.__version__ = "1.0.0"`.

Add `build/` and `dist/` to the Python artifact section of `.gitignore`; release artifacts remain local unless the user separately requests publication.

- [ ] **Step 4: Reinstall and run artifact tests**

```powershell
python -m pip install -e ".[dev,browser,ui,release]"
pytest tests/test_release_artifact.py -q
```

Expected: PASS; built wheel names all required resources.

- [ ] **Step 5: Commit package metadata**

```powershell
git add pyproject.toml .gitignore work_hunter/__init__.py tests/test_release_artifact.py
git commit -m "build: package Work Hunter 1.0 resources"
```

### Task 2: Accurate Installed-Package Doctor Status

**Files:**
- Modify: `work_hunter/services.py` (`doctor` and package helper)
- Test: `tests/test_services.py`
- Test: `tests/test_cli_contract.py`

**Interfaces:**
- Produces: `_package_details(package_root: Path | None = None) -> dict[str, Any]`.
- Doctor reports version, install mode, static/migration resource status, and `config_missing`.

- [ ] **Step 1: Add failing doctor resource tests**

```python
def test_doctor_reports_version_install_mode_and_runtime_resources(tmp_path):
    doctor = WorkHunter(tmp_path).doctor()
    package = doctor["core"]["package"]
    assert package["version"] == "1.0.0"
    assert package["install_mode"] in {"editable", "wheel", "source"}
    assert package["static"]["status"] == "ok"
    assert package["migrations"]["status"] == "ok"
    assert "config_missing" in doctor["warnings"]


def test_doctor_blocks_when_package_resources_are_missing(monkeypatch, tmp_path):
    empty_package = tmp_path / "empty-package"
    empty_package.mkdir()
    monkeypatch.setattr("work_hunter.services.PACKAGE_ROOT", empty_package)
    doctor = WorkHunter(tmp_path / "runtime").doctor()
    assert doctor["status"] == "blocked"
    assert "missing_ui_static" in doctor["blocked"]
    assert "missing_migrations" in doctor["blocked"]
```

- [ ] **Step 2: Verify RED**

Run:

```powershell
pytest tests/test_services.py::test_doctor_reports_version_install_mode_and_runtime_resources tests/test_services.py::test_doctor_blocks_when_package_resources_are_missing -q
```

Expected: package detail keys are absent and missing resources do not block.

- [ ] **Step 3: Implement package metadata/resource inspection**

Add imports for `importlib.metadata`, `json`, and `__version__`, define `PACKAGE_ROOT = Path(__file__).resolve().parent`, and add:

```python
def _package_details(package_root: Path | None = None) -> dict[str, Any]:
    package_root = package_root or PACKAGE_ROOT
    version = __version__
    install_mode = "source"
    try:
        distribution = importlib.metadata.distribution("work-hunter")
        direct_url_text = distribution.read_text("direct_url.json") or ""
        if direct_url_text:
            direct_url = json.loads(direct_url_text)
            if bool((direct_url.get("dir_info") or {}).get("editable")):
                install_mode = "editable"
            else:
                install_mode = "wheel"
                version = distribution.version
        else:
            install_mode = "wheel"
            version = distribution.version
    except (importlib.metadata.PackageNotFoundError, json.JSONDecodeError):
        pass

    static_required = ("index.html", "app.js", "app.css", "manifest.json", "sw.js")
    static_dir = package_root / "web" / "static"
    missing_static = [name for name in static_required if not (static_dir / name).is_file()]
    migrations_dir = package_root / "migrations"
    migrations = sorted(path.name for path in migrations_dir.glob("*.sql"))
    return {
        "status": "ok" if not missing_static and migrations else "error",
        "version": version,
        "install_mode": install_mode,
        "editable": install_mode == "editable",
        "static": {"status": "ok" if not missing_static else "missing", "missing": missing_static},
        "migrations": {"status": "ok" if migrations else "missing", "files": migrations},
    }
```

Call this once in `doctor`; append stable blocked codes for missing static/migrations and warning `config_missing` when the config file does not exist. Add `work-hunter init` as the first next action in that case.

- [ ] **Step 4: Run doctor service/CLI/MCP tests**

```powershell
pytest tests/test_services.py tests/test_cli_contract.py tests/test_hh_agent_mcp.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit truthful doctor reporting**

```powershell
git add work_hunter/services.py tests/test_services.py tests/test_cli_contract.py
git commit -m "fix: verify installed runtime resources"
```

### Task 3: Remove SQLite And Transport Type Errors At Their Source

**Files:**
- Modify: `work_hunter/storage.py`
- Modify: `work_hunter/hh_agent/telegram_bot.py`
- Test: existing storage and Telegram tests

**Interfaces:**
- Produces: `_required_lastrowid(cursor: sqlite3.Cursor) -> int`.
- No behavior change for successful inserts.

- [ ] **Step 1: Record the failing type gate**

Run:

```powershell
mypy work_hunter/storage.py work_hunter/hh_agent/telegram_bot.py
```

Expected: 21 `int | None` conversion errors.

- [ ] **Step 2: Add a cursor invariant helper and regression test**

Add to storage:

```python
def _required_lastrowid(cursor: sqlite3.Cursor) -> int:
    value = cursor.lastrowid
    if value is None:
        raise RuntimeError("SQLite INSERT did not produce a row id")
    return int(value)
```

Add a unit test using a fake cursor with `lastrowid=None` and assert the stable `RuntimeError`; assert a fake value of 42 returns 42.

- [ ] **Step 3: Replace all unsafe cursor conversions and narrow Telegram ID**

Replace the 20 `return int(cur.lastrowid)`/`return int(cursor.lastrowid)` occurrences with `_required_lastrowid(...)`.

Change Telegram parsing to narrow before conversion:

```python
def _telegram_user_id(container: dict[str, Any]) -> int | None:
    user = container.get("from") or {}
    value = user.get("id") if isinstance(user, dict) else None
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
```

- [ ] **Step 4: Run type and behavior suites**

```powershell
mypy work_hunter/storage.py work_hunter/hh_agent/telegram_bot.py
pytest tests/test_storage.py tests/test_storage_backbone.py tests/test_hh_agent_telegram.py -q
```

Expected: both commands PASS.

- [ ] **Step 5: Commit typed insert invariants**

```powershell
git add work_hunter/storage.py work_hunter/hh_agent/telegram_bot.py tests/test_storage.py
git commit -m "fix: type SQLite insert identifiers"
```

### Task 4: Make Services, Web, And CLI Mypy-Clean

**Files:**
- Modify: `work_hunter/services.py`
- Modify: `work_hunter/web/server.py`
- Modify: `work_hunter/cli.py`
- Test: existing full suite

**Interfaces:**
- No public behavior change; local variables and result mappings gain accurate types.

- [ ] **Step 1: Capture the remaining error list**

```powershell
mypy work_hunter --show-error-codes
```

Expected: current non-storage errors in `services.py`, `web/server.py`, and `cli.py`; requests-stub errors disappear after installing dev extras.

- [ ] **Step 2: Apply the exact service type corrections**

Use these code-level changes:

```python
# Form review branches and strategy branches
result: dict[str, Any]

# URL salary parsing
salary=_optional_int((params.get("salary") or [None])[0]),

# PersonaContext is concrete
persona=persona.to_dict(),

# Typed max key
top_reason = max(skipped_by_reason, key=lambda reason: skipped_by_reason[reason])

# Do not reuse one loop variable for different dataclasses
for outbox_item in outbox:
    outbox_by_status[outbox_item.status] = outbox_by_status.get(outbox_item.status, 0) + 1
for webhook in webhooks:
    webhooks_by_status[webhook.status] = webhooks_by_status.get(webhook.status, 0) + 1

# Optional integer from untyped mapping
limit=_optional_int(params.get("limit")),

# Result count from object-valued mapping
count = _optional_int(result.get("count")) or 0

# Export locals cannot reuse Job-typed names
job_data = item.get("job")
job_payload = job_data if isinstance(job_data, dict) else {}

# Report shape is heterogeneous
report: dict[str, Any] = {
    "status": "ok",
    "since": since,
    "daily": self.daily_report(limit=20),
    "stats": self.storage.get_stats(),
    "doctor": self.doctor(),
}
```

Annotate `query_results: list[dict[str, Any]]` and other heterogeneous result mappings at declaration rather than casting at each use.

- [ ] **Step 3: Apply exact web/CLI narrowing and variable names**

In `web/server.py`, rename bytes/dict/string locals (`csv_payload`, `job_payload`, `chat_result`, `trend_result`, `behavior_result`) instead of reusing `payload` or `result`. Use `_optional_int(body.get("limit"))`, `_optional_int(body.get("resume_id"))`, and:

```python
def _optional_int_arg(query: dict[str, list[str]], name: str) -> int | None:
    values = query.get(name)
    if not values or values[0] == "":
        return None
    return int(values[0])
```

In `cli.py`, rename parser-builder locals `score` to `score_parser` and `letter` to `letter_parser`; rename runtime values to `score_value` and `letter_text` in both flat and nested apply handlers.

- [ ] **Step 4: Run whole-project type, lint, and tests**

```powershell
mypy work_hunter
ruff check work_hunter tests
pytest -q
```

Expected: mypy reports `Success: no issues found`; Ruff and all tests pass.

- [ ] **Step 5: Commit type correctness**

```powershell
git add work_hunter/services.py work_hunter/web/server.py work_hunter/cli.py
git commit -m "fix: make application types consistent"
```

### Task 5: Build A Non-Root Wheel-Based Docker Image

**Files:**
- Modify: `Dockerfile`
- Modify: `.dockerignore`
- Test: `tests/test_release_artifact.py`

**Interfaces:**
- Runtime entry point remains `work-hunter` with safe `hh-auth-status` default.
- Runtime user is UID/GID 10001 and owns `/data`.

- [ ] **Step 1: Add failing Docker contract tests**

Add to `tests/test_release_artifact.py`:

```python
def test_dockerfile_installs_wheel_with_dependencies_as_non_root():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "python -m build --wheel" in dockerfile
    assert "pip install /tmp/work_hunter" in dockerfile
    assert "--no-deps ." not in dockerfile
    assert "USER 10001:10001" in dockerfile
    assert 'CMD ["--root", "/data", "hh-auth-status"]' in dockerfile


def test_dockerignore_excludes_private_build_context():
    patterns = set((ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines())
    for required in {
        ".git", ".research", ".playwright-mcp", "external", ".env*",
        "*.har", "*.session", "*.cookies", "*.pem", "*.key", "outputs",
    }:
        assert required in patterns
```

- [ ] **Step 2: Verify RED**

Run: `pytest tests/test_release_artifact.py -q`

Expected: Docker contract tests fail.

- [ ] **Step 3: Replace Dockerfile and harden context**

Replace `Dockerfile` with:

```dockerfile
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1
WORKDIR /build

RUN python -m pip install --upgrade "pip>=26.1.2" "build>=1.2,<2"

COPY pyproject.toml README.md ./
COPY work_hunter ./work_hunter
RUN python -m build --wheel --outdir /dist

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    QT_QUICK_BACKEND=software \
    WORK_HUNTER_ROOT=/data \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl tini \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /dist/work_hunter-1.0.0-py3-none-any.whl /tmp/
RUN python -m pip install --upgrade "pip>=26.1.2" \
    && python -m pip install /tmp/work_hunter-1.0.0-py3-none-any.whl \
    && rm /tmp/work_hunter-1.0.0-py3-none-any.whl

ARG INSTALL_PLAYWRIGHT=false
RUN if [ "$INSTALL_PLAYWRIGHT" = "true" ]; then \
        python -m pip install "playwright>=1.45,<2" \
        && python -m playwright install --with-deps chromium \
        && chmod -R a+rX /ms-playwright; \
    fi

RUN groupadd --gid 10001 workhunter \
    && useradd --uid 10001 --gid 10001 --create-home workhunter \
    && mkdir -p /data \
    && chown -R 10001:10001 /data

USER 10001:10001
VOLUME ["/data"]
ENTRYPOINT ["/usr/bin/tini", "--", "work-hunter"]
CMD ["--root", "/data", "hh-auth-status"]
```

Replace `.dockerignore` with:

```text
.git
.github
.codex_compare
.compare-forks
.compare-hh-applicant-tool
.opencode
.pytest_cache
.mypy_cache
.ruff_cache
.playwright-mcp
.research
.venv
.work-hunter
external
outputs
build
dist
__pycache__
*.pyc
*.pyo
*.egg-info
.env*
*.db
*.sqlite
*.sqlite3
*.log
*.har
*.session
*.cookies
*.pem
*.key
*.p12
*.pfx
```

- [ ] **Step 4: Run contract tests and Docker smoke when daemon is available**

```powershell
pytest tests/test_release_artifact.py -q
docker.exe version
docker.exe build --pull -t work-hunter:1.0.0 .
docker.exe run --rm work-hunter:1.0.0
```

Expected: contract tests pass. With a daemon, build succeeds and run returns structured `missing_access_token` status without an import error or mutation. If daemon is unavailable, preserve the exact `docker version` error for the release report.

- [ ] **Step 5: Commit container release path**

```powershell
git add Dockerfile .dockerignore tests/test_release_artifact.py
git commit -m "build: ship non-root wheel Docker image"
```

### Task 6: CI, Changelog, README, And Historical Whitespace

**Files:**
- Create: `.github/workflows/ci.yml`
- Create: `CHANGELOG.md`
- Create: `docs/releases/1.0.0-checklist.md`
- Modify: `README.md`
- Modify: `docs/plans/МЕГА МОНСТР ПЛАНя.md`
- Modify: `tests/test_cli_contract.py`
- Modify: `tests/test_storage_backbone.py`

**Interfaces:**
- CI verifies but never publishes.
- Release checklist records commands without secrets or absolute machine paths.

- [ ] **Step 1: Add the CI workflow**

Create `.github/workflows/ci.yml` with read-only permissions and jobs:

```yaml
name: CI

on:
  push:
  pull_request:

permissions:
  contents: read

jobs:
  tests:
    strategy:
      matrix:
        python-version: ["3.11", "3.12"]
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
          cache: pip
      - run: python -m pip install --upgrade "pip>=26.1.2"
      - run: python -m pip install -e ".[dev,ui]"
      - run: python -m pytest -q --ignore=tests/test_web_ui_browser.py

  quality:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
      - run: python -m pip install --upgrade "pip>=26.1.2"
      - run: python -m pip install -e ".[dev,release]"
      - run: ruff check .
      - run: mypy work_hunter
      - run: git diff --check
      - run: python -m build
      - run: python -m pip check
      - run: python -m pip_audit

  browser:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
      - run: python -m pip install --upgrade "pip>=26.1.2"
      - run: python -m pip install -e ".[dev,browser,ui]"
      - run: python -m playwright install --with-deps chromium
      - run: python -m pytest tests/test_web_ui_browser.py -q
```

- [ ] **Step 2: Write release documentation and remove known whitespace defects**

Create `CHANGELOG.md` with this release entry:

```markdown
# Changelog

## 1.0.0 - 2026-07-10

### Safety

- Require literal confirmation for every HH mutation, including API Lab and resume operations.
- Reject cross-origin, non-JSON, DNS-rebinding, and non-loopback cockpit mutations.
- Preserve masked configuration secrets during UI updates.

### Data correctness

- Persist OAuth token rotation atomically and isolate scores by active profile.
- Enforce SQLite foreign keys and atomic migrations.
- Preserve query-based vacancy identity and honor ghost-job age.

### Browser cockpit

- Preserve resume fields, target ghost rows explicitly, and default agent preflight to dry mode.
- Add keyboard operation, resilient loading, safe rendering, and offline assets.

### Packaging and verification

- Package UI and SQL resources in the 1.0.0 wheel.
- Add non-root wheel-based Docker image, Python 3.11/3.12 CI, mypy, browser, and dependency-audit gates.
```

Add README sections with these exact commands:

````markdown
## Development And Release Checks

```powershell
python -m pip install --upgrade "pip>=26.1.2"
python -m pip install -e ".[dev,browser,ui,release]"
python -m playwright install chromium
pytest -q
ruff check .
mypy work_hunter
python -m build
```

The cockpit is loopback-only. Real HH mutations require a literal confirmation flag; strings such as `"true"` do not authorize them. Release verification never reads the repository's private `.work-hunter` directory.
````

Create `docs/releases/1.0.0-checklist.md`:

```markdown
# Work Hunter 1.0.0 Release Checklist

- [ ] Full pytest suite
- [ ] Headless Chromium UI suite
- [ ] Ruff
- [ ] mypy
- [ ] git diff --check
- [ ] Wheel and sdist build
- [ ] Clean-wheel CLI, doctor, UI, and MCP smoke
- [ ] pip check and runtime pip-audit
- [ ] Tracked secret/path scan
- [ ] Docker build and safe default smoke, or exact daemon blocker recorded

## Results

Results are written after the commands run. No secrets or absolute machine paths are recorded.
```

Remove the two trailing spaces on line 3 of `docs/plans/МЕГА МОНСТР ПЛАНя.md` and the extra final blank lines in `tests/test_cli_contract.py` and `tests/test_storage_backbone.py`.

- [ ] **Step 3: Validate workflow syntax, docs references, and whitespace**

```powershell
rg.exe -n "0\.1\.0|starlette>=0\.27|pip install -e \\." README.md CHANGELOG.md pyproject.toml work_hunter
git diff --check main...HEAD
```

Expected: no stale release version/dependency instructions; diff check exits zero. GitHub performs the authoritative workflow parse on the first push, but this plan does not push.

- [ ] **Step 4: Run documentation-adjacent CLI tests**

```powershell
pytest tests/test_cli_contract.py tests/test_release_artifact.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit release automation and docs**

```powershell
git add .github/workflows/ci.yml CHANGELOG.md docs/releases/1.0.0-checklist.md README.md 'docs/plans/МЕГА МОНСТР ПЛАНя.md' tests/test_cli_contract.py tests/test_storage_backbone.py
git commit -m "docs: prepare Work Hunter 1.0 release"
```

### Task 7: Clean Artifact And Full Release Verification

**Files:**
- Modify: `docs/releases/1.0.0-checklist.md` with exact results only.

**Interfaces:**
- Consumes every prior safety, data, UI, and release task.
- Produces final evidence for source, wheel, browser, MCP, dependencies, secrets, and Docker.

- [ ] **Step 1: Run all source gates from the project environment**

```powershell
python -m pip install --upgrade "pip>=26.1.2"
python -m pip install -e ".[dev,browser,ui,release]"
python -m playwright install chromium
pytest -q
pytest tests/test_web_ui_browser.py -q
ruff check .
mypy work_hunter
git diff --check
python -m build
python -m pip check
```

Expected: every command exits zero.

- [ ] **Step 2: Install the wheel into a clean temporary environment**

```powershell
$wheel = (Resolve-Path 'dist\work_hunter-1.0.0-py3-none-any.whl').Path
$smoke = Join-Path $env:TEMP ('work-hunter-1.0-smoke-' + [guid]::NewGuid().ToString('N'))
$venv = Join-Path $smoke 'venv'
$root = Join-Path $smoke 'runtime'
python -m venv $venv
& (Join-Path $venv 'Scripts\python.exe') -m pip install --upgrade 'pip>=26.1.2'
& (Join-Path $venv 'Scripts\python.exe') -m pip install $wheel
& (Join-Path $venv 'Scripts\work-hunter.exe') --help
& (Join-Path $venv 'Scripts\work-hunter.exe') --root $root init
& (Join-Path $venv 'Scripts\work-hunter.exe') --root $root doctor --json
Push-Location $smoke
try {
  & (Join-Path $venv 'Scripts\python.exe') -c "import pathlib,sqlite3,sys; db=pathlib.Path(sys.argv[1])/'.work-hunter'/'work_hunter.sqlite3'; conn=sqlite3.connect(db); count=conn.execute('SELECT COUNT(*) FROM schema_migrations').fetchone()[0]; assert count >= 1, count; print(f'migrations={count}')" $root
} finally {
  Pop-Location
}
& (Join-Path $venv 'Scripts\python.exe') -m pip check
```

Expected: CLI succeeds; doctor reports version 1.0.0, `install_mode=wheel`, static/migrations `ok`, and missing HH credentials as a warning rather than a crash.

- [ ] **Step 3: Smoke the installed UI and runtime dependency audit**

Run:

```powershell
$smokePython = Join-Path $venv 'Scripts\python.exe'
$smokeExe = Join-Path $venv 'Scripts\work-hunter.exe'
$listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
$listener.Start()
$port = ([Net.IPEndPoint]$listener.LocalEndpoint).Port
$listener.Stop()
$stdout = Join-Path $smoke 'ui.stdout.log'
$stderr = Join-Path $smoke 'ui.stderr.log'
$process = Start-Process -FilePath $smokeExe -ArgumentList @(
  '--root', $root, 'ui', '--host', '127.0.0.1', '--port', [string]$port
) -PassThru -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr
try {
  $ready = $false
  foreach ($attempt in 1..50) {
    try {
      $doctorResponse = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$port/api/doctor" -TimeoutSec 1
      $ready = $true
      break
    } catch {
      Start-Sleep -Milliseconds 100
    }
  }
  if (-not $ready) { throw 'Installed UI did not start' }
  $rootResponse = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$port/" -TimeoutSec 5
  $scriptResponse = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$port/app.js" -TimeoutSec 5
  if ($rootResponse.StatusCode -ne 200 -or $scriptResponse.StatusCode -ne 200 -or $doctorResponse.StatusCode -ne 200) {
    throw 'Installed UI smoke returned a non-200 status'
  }
} finally {
  if (-not $process.HasExited) { Stop-Process -Id $process.Id -Force }
}
& $smokePython -m pip install 'pip-audit>=2.10,<3'
& $smokePython -m pip_audit
```

Expected: all three HTTP statuses are 200 and the audit reports no known vulnerability in runtime dependencies.

- [ ] **Step 4: Run MCP and secret/path scans**

```powershell
pytest tests/test_mcp_safety.py tests/test_hh_agent_mcp.py -q
Push-Location $smoke
try {
@'
import asyncio
import json
import sys
import tempfile

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="work-hunter-mcp-") as root:
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "work_hunter", "--root", root, "mcp"],
        )
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
    names = {tool.name for tool in tools.tools}
    required = {"doctor", "list_jobs", "hh_research_and_apply"}
    if not required.issubset(names):
        raise SystemExit(f"missing MCP tools: {sorted(required - names)}")
    print(json.dumps({"status": "ok", "tools": len(names)}))


asyncio.run(main())
'@ | & $smokePython -
} finally {
  Pop-Location
}
git grep -n -I -E "(-----BEGIN [A-Z ]*PRIVATE KEY-----|sk-[A-Za-z0-9_-]{20,}|sk-or-v1-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9_]{20,})"
git ls-files | rg.exe "(^|/)(\.work-hunter|external|\.research|\.playwright-mcp)(/|$)|\.(db|sqlite3|har|pem|key)$"
```

Expected: MCP tests pass; installed-wheel MCP reports `status=ok`; both scans have no output.

- [ ] **Step 5: Verify Docker or record the sole environmental gap**

```powershell
docker.exe version
docker.exe build --pull -t work-hunter:1.0.0 .
docker.exe run --rm work-hunter:1.0.0
```

Expected with daemon: image builds and safe auth status runs. Without daemon: record the exact daemon connection error and do not claim Docker verification.

- [ ] **Step 6: Record exact results and commit release evidence**

Write command, exit code, test count, artifact filename, dependency-audit result, wheel UI HTTP statuses, MCP result, and Docker result to `docs/releases/1.0.0-checklist.md`. Never include absolute temp paths or secrets.

```powershell
git add docs/releases/1.0.0-checklist.md
git commit -m "test: verify Work Hunter 1.0 release"
```

- [ ] **Step 7: Confirm final repository state**

```powershell
git status --short --branch
git log -12 --oneline --decorate
```

Expected: clean working tree, branch ahead only by intentional release commits. Do not push or create a public release.
