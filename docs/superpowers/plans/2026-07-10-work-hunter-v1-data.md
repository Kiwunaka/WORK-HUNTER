# Work Hunter 1.0 Data Integrity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make token rotation, profile scoring, SQLite integrity, vacancy identity, ghost timing, and HH network fallback deterministic and durable.

**Architecture:** Keep `WorkHunter` as the application facade and `Storage` as the only SQLite owner. Add callback-backed HH identity persistence, pass profile IDs explicitly through storage reads, serialize schema work with SQLite transactions, and normalize external identifiers/errors at adapter boundaries.

**Tech Stack:** Python 3.11+, SQLite, requests, pytest, dataclasses, standard-library URL and hashing modules.

## Global Constraints

- All tests use temporary config and database paths.
- Token values in assertions are synthetic and never logged.
- A failed migration leaves neither partial schema nor a migration-version row.
- Scores for different profiles coexist; `default` is used only when no named active profile exists.
- Canonical URL identity keeps meaningful query parameters and removes tracking only.
- Authentication/challenge errors never silently fall back to browser scraping.

---

### Task 1: Atomically Persist Rotated HH Identity

**Files:**
- Modify: `work_hunter/config.py` (`save_config`)
- Modify: `work_hunter/hh_transport/backends.py`
- Modify: `work_hunter/sources/hh.py` (`HHApplyClient.__init__`)
- Modify: `work_hunter/services.py` (HH client factory and token refresh)
- Test: `tests/test_hh_auth.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `CallbackConfigBackend(config, save_callback)` implementing `ConfigBackend`.
- Produces: `WorkHunter._persist_hh_identity_patch(patch)` and `WorkHunter._hh_client()`.
- Changes: `HHApplyClient(config, *, backend: ConfigBackend | None = None)`.

- [ ] **Step 1: Write failing atomic-save and rotation tests**

Add an atomic-write test that monkeypatches `os.replace`, verifies the temporary file is in the config directory, contains complete JSON, and that the destination is replaced once. Add this rotation regression to `tests/test_hh_auth.py` using the file's existing `FakeResponse` helper:

```python
def test_rotated_refresh_token_persists_to_client_app_and_disk(monkeypatch, tmp_path):
    app = WorkHunter(tmp_path)
    app.save_hh_account_profile(
        "personal",
        access_token="expired-access",
        refresh_token="old-refresh",
        access_expires_at="2000-01-01T00:00:00+00:00",
    )
    app.use_hh_account_profile("personal")

    token_response = FakeResponse(200, {
        "access_token": "new-access",
        "refresh_token": "new-refresh",
        "expires_at": "2031-01-01T00:00:00+00:00",
    })
    me_response = FakeResponse(200, {"id": "me-1"})
    monkeypatch.setattr("requests.request", lambda *args, **kwargs: token_response)
    monkeypatch.setattr(
        "requests.sessions.Session.request",
        lambda *args, **kwargs: me_response,
    )

    client = app._hh_client()
    assert client.whoami() == {"id": "me-1"}
    assert client.session.identity.refresh_token == "new-refresh"
    assert app.hh_config()["refresh_token"] == "new-refresh"

    reloaded = WorkHunter(tmp_path)
    assert reloaded.hh_config()["access_token"] == "new-access"
    assert reloaded.hh_config()["refresh_token"] == "new-refresh"
    assert reloaded._hh_client().session.identity.refresh_token == "new-refresh"
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```powershell
pytest tests/test_config.py::test_save_config_replaces_complete_temp_file tests/test_hh_auth.py::test_rotated_refresh_token_persists_to_client_app_and_disk -q
```

Expected: atomic test fails because config is written in place; rotation test fails because `HHApplyClient` uses a detached dict backend.

- [ ] **Step 3: Implement atomic config save and callback persistence**

Replace `save_config` with same-directory write-and-replace logic:

```python
def save_config(path: str | Path, config: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(config, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
```

Add to `backends.py`:

```python
class CallbackConfigBackend:
    def __init__(
        self,
        config: dict[str, Any],
        save_callback: Callable[[dict[str, Any]], None],
    ):
        self.config = config
        self.save_callback = save_callback

    def load(self) -> dict[str, Any]:
        return dict(self.config)

    def save(self, patch: dict[str, Any]) -> None:
        self.config.update(patch)
        self.save_callback(dict(patch))
```

Pass the backend from `HHApplyClient` to `HHApiSession`. In `WorkHunter._persist_hh_identity_patch`, update the active account mapping (including the default source config), then call atomic `save_config`. Replace direct `HHApplyClient(self.hh_config())` construction in `services.py` with `_hh_client()`.

Every test fake that monkeypatches `work_hunter.services.HHApplyClient` changes its constructor to accept the optional backend without using it:

```python
def __init__(self, config, *, backend=None):
    self.config = config
    self.backend = backend
```

- [ ] **Step 4: Run auth/config/transport suites**

Run:

```powershell
pytest tests/test_config.py tests/test_hh_auth.py tests/test_hh_transport.py -q
```

Expected: PASS; a second `WorkHunter` reads `new-refresh`.

- [ ] **Step 5: Commit token durability**

```powershell
git add work_hunter/config.py work_hunter/hh_transport/backends.py work_hunter/sources/hh.py work_hunter/services.py tests/test_config.py tests/test_hh_auth.py
git commit -m "fix: persist rotated HH credentials"
```

### Task 2: Active-Profile Score Isolation

**Files:**
- Modify: `work_hunter/services.py` (`active_profile_id`, scoring and reads)
- Modify: `work_hunter/storage.py` (`get_job`, `list_jobs`)
- Modify: `work_hunter/web/server.py` (stats job read)
- Test: `tests/test_services.py`
- Test: `tests/test_storage.py`

**Interfaces:**
- Produces: `WorkHunter.active_profile_id() -> str`.
- Changes: `Storage.get_job` adds `profile_id: str = "default"`; `Storage.list_jobs` adds the same keyword-only argument to its existing filters.

- [ ] **Step 1: Add the two-profile regression**

Add to `tests/test_services.py`:

```python
def test_scores_for_two_profiles_coexist_and_reads_use_active_profile(tmp_path):
    app = WorkHunter(tmp_path)
    app.config["profiles"]["python"] = {
        "desired_roles": ["python"], "must_have_skills": ["python"],
        "nice_to_have_skills": [], "stop_words": [], "salary_min": 0,
    }
    app.config["profiles"]["java"] = {
        "desired_roles": ["java"], "must_have_skills": ["java"],
        "nice_to_have_skills": [], "stop_words": [], "salary_min": 0,
    }
    job_id = app.storage.upsert_job(
        Job(
            source="x",
            source_id="1",
            url="https://example.test/1",
            title="Python Engineer",
            description="Python",
        )
    )

    app.switch_profile("python")
    app.score_jobs()
    python_score = app.get_job(job_id).score

    app.switch_profile("java")
    app.score_jobs()
    java_score = app.get_job(job_id).score

    rows = app.storage.query_readonly(
        f"SELECT profile_id, total_score FROM job_scores "
        f"WHERE job_id = {job_id} ORDER BY profile_id"
    )
    assert [row["profile_id"] for row in rows] == ["java", "python"]
    assert python_score.profile_id == "python"
    assert java_score.profile_id == "java"
    assert python_score.total_score > java_score.total_score

    app.switch_profile("python")
    assert app.list_jobs()[0].score.profile_id == "python"
    assert json.loads(app.export_jobs())[0]["score"]["profile_id"] == "python"
```

- [ ] **Step 2: Verify RED**

Run: `pytest tests/test_services.py::test_scores_for_two_profiles_coexist_and_reads_use_active_profile -q`

Expected: only `profile_id=default` exists and the second scoring overwrites the first.

- [ ] **Step 3: Thread the active profile ID through writes and reads**

Add:

```python
def active_profile_id(self) -> str:
    selected = self.config.get("profile", "default")
    return selected if isinstance(selected, str) and selected else "default"
```

Before `save_score`, set `score.profile_id = self.active_profile_id()`. Add a `profile_id` query parameter to `Storage.get_job` and `Storage.list_jobs`; bind it in the score JOIN and pass it to `get_score`. Make `WorkHunter.get_job/list_jobs/export_jobs` use the active ID. HTTP routes call the facade rather than raw storage when they need scored jobs.

- [ ] **Step 4: Run service/storage/export tests**

Run:

```powershell
pytest tests/test_services.py tests/test_storage.py tests/test_web_ui_contract.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit profile isolation**

```powershell
git add work_hunter/services.py work_hunter/storage.py work_hunter/web/server.py tests/test_services.py tests/test_storage.py
git commit -m "fix: isolate scores by active profile"
```

### Task 3: Foreign Keys And Atomic Concurrent Migrations

**Files:**
- Modify: `work_hunter/storage.py` (connection setup and schema runner)
- Test: `tests/test_storage_backbone.py`
- Test: `tests/test_storage.py`

**Interfaces:**
- Changes: `Storage(path, *, migrations_dir: str | Path | None = None)`.
- Produces: `_schema_transaction()` and `_execute_sql_script(script)` internal helpers.

- [ ] **Step 1: Add failing integrity, concurrency, and rollback tests**

Add the following imports and tests to `tests/test_storage_backbone.py`: `sqlite3`, `threading`, `ThreadPoolExecutor`, and `pytest`.

```python
def test_storage_enables_foreign_keys_and_busy_timeout(tmp_path):
    storage = Storage(tmp_path / "db.sqlite3")
    assert storage.conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert storage.conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    with pytest.raises(sqlite3.IntegrityError):
        storage.save_application(999, "applied")


def test_concurrent_storage_initialization_is_serialized(tmp_path):
    path = tmp_path / "db.sqlite3"
    barrier = threading.Barrier(8)

    def construct(_: int) -> None:
        barrier.wait()
        storage = Storage(path)
        storage.close()

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(construct, index) for index in range(8)]
        for future in futures:
            future.result()

    storage = Storage(path)
    assert storage.conn.execute(
        "SELECT COUNT(*) FROM schema_migrations"
    ).fetchone()[0] == 1


def test_failed_migration_rolls_back_schema_and_version(tmp_path):
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "9999_broken.sql").write_text(
        "CREATE TABLE partial_schema(id INTEGER); THIS IS INVALID;",
        encoding="utf-8",
    )
    path = tmp_path / "db.sqlite3"

    with pytest.raises(sqlite3.Error):
        Storage(path, migrations_dir=migrations)

    conn = sqlite3.connect(path)
    names = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert "partial_schema" not in names
```

- [ ] **Step 2: Run backbone tests and verify RED**

Run: `pytest tests/test_storage_backbone.py -q`

Expected: foreign keys report 0, concurrent constructors can raise duplicate-column errors, and the partial table survives.

- [ ] **Step 3: Replace implicit `executescript` commits with one immediate transaction**

Enable before schema work:

```python
self.conn = sqlite3.connect(self.path)
self.conn.row_factory = sqlite3.Row
self.conn.execute("PRAGMA foreign_keys = ON")
self.conn.execute("PRAGMA busy_timeout = 5000")
```

Implement:

```python
@contextmanager
def _schema_transaction(self) -> Iterator[None]:
    self.conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except Exception:
        self.conn.rollback()
        raise
    else:
        self.conn.commit()


def _execute_sql_script(self, script: str) -> None:
    pending: list[str] = []
    for line in script.splitlines(keepends=True):
        pending.append(line)
        candidate = "".join(pending).strip()
        if candidate and sqlite3.complete_statement(candidate):
            self.conn.execute(candidate)
            pending.clear()
    if "".join(pending).strip():
        raise sqlite3.OperationalError("Incomplete SQL migration statement")
```

Move the existing base schema string to `BASE_SCHEMA_SQL`. In `_migrate`, enter `_schema_transaction`, run the base schema, versioned files, version inserts, and compatibility `_ensure_column` calls. Remove commits from nested migration helpers. Resolve `migrations_dir` from the constructor override or `Path(__file__).with_name("migrations")`. Close the connection before re-raising constructor errors.

- [ ] **Step 4: Run storage tests twice to catch idempotency/race regressions**

Run:

```powershell
pytest tests/test_storage_backbone.py tests/test_storage.py -q
pytest tests/test_storage_backbone.py tests/test_storage.py -q
```

Expected: both runs PASS.

- [ ] **Step 5: Commit SQLite integrity**

```powershell
git add work_hunter/storage.py tests/test_storage_backbone.py tests/test_storage.py
git commit -m "fix: make SQLite schema changes atomic"
```

### Task 4: Canonical Query-Aware Vacancy Identity

**Files:**
- Modify: `work_hunter/sources/common.py`
- Modify: `work_hunter/sources/public_boards.py`
- Modify: `work_hunter/services.py` (fallback URL handling)
- Test: `tests/test_public_boards.py`

**Interfaces:**
- Produces: `canonicalize_job_url(url: str) -> str`.
- Fallback source IDs become `<source>-<first-24-sha256-hex>`.

- [ ] **Step 1: Add query identity regression**

```python
def test_query_identity_is_distinct_and_tracking_params_are_canonicalized():
    first = canonicalize_job_url(
        "https://app.rvc.global/vacancy/view?b=2&id=1&utm_source=x#top"
    )
    equivalent = canonicalize_job_url(
        "https://app.rvc.global/vacancy/view?id=1&b=2"
    )
    assert first == equivalent
    assert "utm_source" not in first
    assert "#" not in first

    html = """
      <a href="/vacancy/view?id=1&b=2&utm_source=x">Backend Engineer</a>
      <a href="/vacancy/view?b=2&id=2">Backend Engineer</a>
    """
    jobs = parse_public_board_html(
        html, source="rvc", base_url="https://app.rvc.global"
    )
    assert len(jobs) == 2
    assert len({job.source_id for job in jobs}) == 2
    expected = "rvc-" + hashlib.sha256(first.encode("utf-8")).hexdigest()[:24]
    assert jobs[0].source_id == expected
```

- [ ] **Step 2: Verify RED**

Run: `pytest tests/test_public_boards.py::test_query_identity_is_distinct_and_tracking_params_are_canonicalized -q`

Expected: both anchors collapse to source ID `view`.

- [ ] **Step 3: Implement canonicalization and hashed fallback IDs**

Add to `sources/common.py`:

```python
TRACKING_QUERY_KEYS = frozenset({"gclid", "yclid"})


def canonicalize_job_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    query = [
        (key, value)
        for key, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_")
        and key.lower() not in TRACKING_QUERY_KEYS
    ]
    return urllib.parse.urlunsplit((
        parsed.scheme.lower(),
        parsed.netloc.lower(),
        parsed.path or "/",
        urllib.parse.urlencode(sorted(query), doseq=True),
        "",
    ))
```

Use canonical URL for dedupe. Preserve a source-provided vacancy ID; otherwise hash the canonical URL with SHA-256 and the exact 24-character prefix.

- [ ] **Step 4: Run all public-source parsers**

Run:

```powershell
pytest tests/test_public_boards.py tests/test_sources.py tests/test_hh_level_sources.py -q
```

Expected: PASS after updating only old fallback-ID expectations.

- [ ] **Step 5: Commit identity correctness**

```powershell
git add work_hunter/sources/common.py work_hunter/sources/public_boards.py work_hunter/services.py tests/test_public_boards.py
git commit -m "fix: preserve query-based vacancy identity"
```

### Task 5: Honor Ghost-Job Age

**Files:**
- Modify: `work_hunter/storage.py` (`get_ghost_jobs`)
- Test: `tests/test_storage.py`

**Interfaces:**
- Produces: `_utc_cutoff_days(days: int, *, now: datetime | None = None) -> str`.

- [ ] **Step 1: Add a fresh-vs-old application regression**

```python
def test_get_ghost_jobs_honors_days_cutoff(tmp_path):
    storage = Storage(tmp_path / "db.sqlite3")
    old_id = storage.upsert_job(Job(source="x", source_id="old", url="u1", title="Old"))
    fresh_id = storage.upsert_job(Job(source="x", source_id="fresh", url="u2", title="Fresh"))
    for job_id in (old_id, fresh_id):
        storage.set_status(job_id, "applied")
        storage.save_application(job_id, "applied")

    now = datetime.now(timezone.utc).replace(microsecond=0)
    storage.conn.execute(
        "UPDATE applications SET applied_at=? WHERE job_id=?",
        ((now - timedelta(days=31)).isoformat(), old_id),
    )
    storage.conn.execute(
        "UPDATE applications SET applied_at=? WHERE job_id=?",
        ((now - timedelta(days=1)).isoformat(), fresh_id),
    )
    storage.conn.commit()

    assert [job.id for job in storage.get_ghost_jobs(days=30)] == [old_id]
```

- [ ] **Step 2: Verify RED**

Run: `pytest tests/test_storage.py::test_get_ghost_jobs_honors_days_cutoff -q`

Expected: both applications are returned because cutoff equals current time.

- [ ] **Step 3: Compute the real UTC cutoff**

```python
def _utc_cutoff_days(days: int, *, now: datetime | None = None) -> str:
    reference = now or datetime.now(timezone.utc)
    cutoff = reference - timedelta(days=max(0, int(days)))
    return cutoff.replace(microsecond=0).isoformat()
```

Use this helper in the existing SQL comparison.

- [ ] **Step 4: Run storage and UI ghost tests**

Run: `pytest tests/test_storage.py tests/test_web_ui_contract.py -q`

Expected: PASS.

- [ ] **Step 5: Commit ghost timing**

```powershell
git add work_hunter/storage.py tests/test_storage.py
git commit -m "fix: honor ghost job age cutoff"
```

### Task 6: Typed Network Errors And Controlled Web Fallback

**Files:**
- Modify: `work_hunter/hh_transport/api_session.py`
- Modify: `work_hunter/sources/hh.py`
- Test: `tests/test_hh_transport.py`
- Test: `tests/test_sources.py`

**Interfaces:**
- Produces: `_network_error(method, path, exc) -> HHTransportError` with code `network_error`.
- Fallback accepts only typed network/parse failures, never auth/challenge outcomes.

- [ ] **Step 1: Add masked network and fallback tests**

```python
def test_api_session_masks_and_wraps_request_exception(monkeypatch):
    def fail(*args, **kwargs):
        raise requests.ConnectionError(
            "https://api.hh.ru/vacancies?access_token=secret"
        )

    monkeypatch.setattr("requests.sessions.Session.request", fail)
    session = HHApiSession({"access_token": "token"})
    with pytest.raises(HHTransportError) as error:
        session.request_json("GET", "/vacancies?access_token=secret")
    assert error.value.code == "network_error"
    assert "GET /vacancies" in str(error.value)
    assert "secret" not in str(error.value)
```

Add to `tests/test_sources.py`:

```python
def test_hh_source_uses_web_fallback_for_request_timeout(monkeypatch):
    fallback_job = Job(
        source="hh",
        source_id="web-1",
        url="https://hh.ru/vacancy/web-1",
        title="Fallback",
    )
    source = HHSource({"access_token": "token", "web_fallback": True})
    monkeypatch.setattr(
        "requests.sessions.Session.request",
        lambda *args, **kwargs: (_ for _ in ()).throw(requests.Timeout("offline")),
    )
    monkeypatch.setattr(source, "_collect_web", lambda profile, limit: [fallback_job])
    assert source.collect({"queries": ["python"]}) == [fallback_job]


def test_hh_source_does_not_relabel_or_fallback_on_auth_error(monkeypatch):
    source = HHSource({"access_token": "token", "web_fallback": True})
    monkeypatch.setattr(
        "work_hunter.sources.hh.HHApiSession.search_vacancies",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            HHAuthError("expired", code="token_expired")
        ),
    )
    monkeypatch.setattr(
        source,
        "_collect_web",
        lambda *args, **kwargs: pytest.fail("auth error must not use web fallback"),
    )
    with pytest.raises(HHAuthError) as error:
        source.collect({"queries": ["python"]})
    assert error.value.code == "token_expired"
```

- [ ] **Step 2: Verify RED**

Run:

```powershell
pytest tests/test_hh_transport.py::test_api_session_masks_and_wraps_request_exception tests/test_sources.py::test_hh_source_uses_web_fallback_for_request_timeout tests/test_sources.py::test_hh_source_does_not_relabel_or_fallback_on_auth_error -q
```

Expected: raw `requests` exceptions escape and bypass fallback.

- [ ] **Step 3: Wrap requests at the transport edge and narrow fallback**

```python
def _network_error(
    method: str,
    path: str,
    exc: requests.RequestException,
) -> HHTransportError:
    safe_path = urllib.parse.urlsplit(path).path or "/"
    return HHTransportError(
        f"{method.upper()} {safe_path} failed: {type(exc).__name__}",
        code="network_error",
        payload={"method": method.upper(), "path": safe_path},
    )
```

Catch `requests.RequestException` around both session requests and token refresh, then raise this error from the original exception. In the HH source, allow web fallback for `HHTransportError.code == "network_error"` and the existing parse failure only. Re-raise `HHAuthError`, challenge, forbidden, validation, and rate-limit outcomes unchanged.

- [ ] **Step 4: Run transport/source tests**

Run:

```powershell
pytest tests/test_hh_transport.py tests/test_sources.py tests/test_hh_operations.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit typed fallback behavior**

```powershell
git add work_hunter/hh_transport/api_session.py work_hunter/sources/hh.py tests/test_hh_transport.py tests/test_sources.py
git commit -m "fix: wrap HH network failures"
```

### Task 7: Data Integrity Gate

**Files:**
- Modify only when the aggregate run exposes a regression owned by Tasks 1-6.

**Interfaces:**
- Consumes all data interfaces above.
- Produces a green data slice.

- [ ] **Step 1: Run the complete data slice**

```powershell
pytest tests/test_hh_auth.py tests/test_hh_transport.py tests/test_services.py tests/test_storage.py tests/test_storage_backbone.py tests/test_public_boards.py tests/test_sources.py -q
```

Expected: PASS.

- [ ] **Step 2: Run lint, type, and whitespace gates for owned files**

```powershell
ruff check work_hunter tests
mypy work_hunter/config.py work_hunter/hh_transport work_hunter/sources work_hunter/storage.py work_hunter/services.py
git diff --check
```

Expected: Ruff and diff check pass. Any remaining repository-wide type errors are handled by the release plan; data-owned errors are fixed here.

- [ ] **Step 3: Commit only aggregate corrections**

```powershell
git add work_hunter tests
git commit -m "test: complete data integrity gate"
```

Skip this commit when aggregate verification changes no files.
