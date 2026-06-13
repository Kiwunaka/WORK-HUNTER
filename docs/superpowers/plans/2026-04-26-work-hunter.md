# Work Hunter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a personal local hybrid job-search assistant with MCP tools and a web UI.

**Architecture:** Create a separate Python package named `work_hunter`. It stores normalized jobs in SQLite, collects from HH/Habr/GeekJob through source adapters, scores jobs against a local profile, exposes CLI/MCP tools, and serves a local web UI on `127.0.0.1`.

**Tech Stack:** Python 3.12, stdlib SQLite/HTTP server, installed MCP Python SDK, pytest.

---

## File Structure

- Create `pyproject.toml`: package metadata and console script.
- Create `work_hunter/__init__.py`: package version.
- Create `work_hunter/__main__.py`: module entrypoint.
- Create `work_hunter/cli.py`: CLI commands.
- Create `work_hunter/config.py`: JSON config defaults/load/save.
- Create `work_hunter/models.py`: dataclasses and serialization helpers.
- Create `work_hunter/storage.py`: SQLite schema and repository methods.
- Create `work_hunter/scoring.py`: deterministic vacancy scoring.
- Create `work_hunter/letters.py`: cover-letter draft templates.
- Create `work_hunter/services.py`: orchestration layer for collectors, scoring, letters, statuses.
- Create `work_hunter/sources/hh.py`: HH OAuth API collector, personal-use HTML fallback, and `hh-applicant-tool` adapter.
- Create `work_hunter/sources/habr.py`: Habr Career RSS/HTML collector.
- Create `work_hunter/sources/geekjob.py`: GeekJob HTML collector and channel registry.
- Create `work_hunter/mcp_server.py`: MCP tool server.
- Create `work_hunter/web/server.py`: local HTTP API and static server.
- Create `work_hunter/web/static/index.html`, `app.css`, `app.js`: local UI.
- Create tests under `tests/`.

### Task 1: Project, Config, Models

**Files:**
- Create: `pyproject.toml`
- Create: `work_hunter/__init__.py`
- Create: `work_hunter/__main__.py`
- Create: `work_hunter/config.py`
- Create: `work_hunter/models.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing config test**

```python
from work_hunter.config import default_config, load_config, save_config

def test_save_and_load_config(tmp_path):
    path = tmp_path / "config.json"
    cfg = default_config()
    cfg["profile"]["queries"] = ["python backend"]
    save_config(path, cfg)
    loaded = load_config(path)
    assert loaded["profile"]["queries"] == ["python backend"]
    assert loaded["sources"]["hh"]["enabled"] is True
```

- [ ] **Step 2: Run the test**

Run: `python -m pytest tests/test_config.py -v`

- [ ] **Step 3: Implement config and models**

Create JSON config helpers with stable defaults and `Job`, `JobScore`, `StatusEvent`, `LetterDraft` dataclasses.

- [ ] **Step 4: Re-run the test**

Run: `python -m pytest tests/test_config.py -v`

### Task 2: SQLite Storage

**Files:**
- Create: `work_hunter/storage.py`
- Test: `tests/test_storage.py`

- [ ] **Step 1: Write storage tests**

```python
from work_hunter.models import Job
from work_hunter.storage import Storage

def test_upsert_job_and_status(tmp_path):
    storage = Storage(tmp_path / "db.sqlite3")
    job = Job(source="hh", source_id="1", url="https://example.test/1", title="Python Developer", company="Acme")
    job_id = storage.upsert_job(job)
    assert storage.upsert_job(job) == job_id
    storage.set_status(job_id, "saved", "looks good")
    loaded = storage.get_job(job_id)
    assert loaded is not None
    assert loaded.status == "saved"
```

- [ ] **Step 2: Implement schema and repository**

Create tables `jobs`, `job_scores`, `job_status`, `letters`, `sources`, and `runs`.

- [ ] **Step 3: Run storage tests**

Run: `python -m pytest tests/test_storage.py -v`

### Task 3: Sources

**Files:**
- Create: `work_hunter/sources/__init__.py`
- Create: `work_hunter/sources/hh.py`
- Create: `work_hunter/sources/habr.py`
- Create: `work_hunter/sources/geekjob.py`
- Test: `tests/test_sources.py`

- [ ] **Step 1: Write parser tests**

```python
from work_hunter.sources.geekjob import parse_geekjob_html
from work_hunter.sources.habr import parse_habr_rss

def test_parse_habr_rss_item():
    xml = """<?xml version="1.0"?><rss><channel><item><title>Python Dev</title><link>https://career.habr.com/vacancies/1</link><description>Acme</description><pubDate>Sun, 26 Apr 2026 10:00:00 +0300</pubDate></item></channel></rss>"""
    jobs = parse_habr_rss(xml)
    assert jobs[0].source == "habr"
    assert jobs[0].title == "Python Dev"

def test_parse_geekjob_html_item():
    html = """<li class="collection-item avatar"><a href="/vacancy/abc" class="title">Backend Dev</a><p class="truncate company-name"><a>Acme</a></p><span class="salary">300K ₽</span><span class="remote-label">remote</span></li>"""
    jobs = parse_geekjob_html(html)
    assert jobs[0].source_id == "abc"
    assert jobs[0].remote is True
```

- [ ] **Step 2: Implement collectors**

Use `urllib.request` for network calls. HH uses the official API when a token is configured and falls back to visible search HTML for personal collection. Habr uses RSS first and HTML fallback. GeekJob parses HTML list pages and falls back to the unfiltered feed if search returns nothing.

- [ ] **Step 3: Run source tests**

Run: `python -m pytest tests/test_sources.py -v`

### Task 4: Scoring And Letters

**Files:**
- Create: `work_hunter/scoring.py`
- Create: `work_hunter/letters.py`
- Test: `tests/test_scoring_letters.py`

- [ ] **Step 1: Write scoring and letter tests**

```python
from work_hunter.letters import draft_cover_letter
from work_hunter.models import Job
from work_hunter.scoring import score_job

def test_score_job_with_matching_skill():
    profile = {"desired_roles": ["backend"], "must_have_skills": ["python"], "nice_to_have_skills": ["fastapi"], "stop_words": ["bitrix"], "remote_only": True}
    job = Job(source="hh", source_id="1", url="u", title="Python Backend Developer", company="Acme", description="FastAPI remote", remote=True)
    score = score_job(job, profile)
    assert score.total_score >= 70
    assert "python" in " ".join(score.reasons).lower()

def test_draft_cover_letter_contains_company_and_title():
    job = Job(source="hh", source_id="1", url="u", title="Python Backend Developer", company="Acme")
    text = draft_cover_letter(job, {"name": "Кандидат", "must_have_skills": ["Python"]})
    assert "Acme" in text
    assert "Python Backend Developer" in text
```

- [ ] **Step 2: Implement scoring and letters**

Scoring must return component scores, reasons, and red flags. Letter drafting must be deterministic and not send anything.

- [ ] **Step 3: Run tests**

Run: `python -m pytest tests/test_scoring_letters.py -v`

### Task 5: Service Layer And CLI

**Files:**
- Create: `work_hunter/services.py`
- Create: `work_hunter/cli.py`
- Create: `work_hunter/__main__.py`
- Test: `tests/test_services.py`

- [ ] **Step 1: Write service test**

```python
from work_hunter.models import Job
from work_hunter.services import WorkHunter

def test_score_existing_jobs(tmp_path):
    app = WorkHunter(root=tmp_path)
    job_id = app.storage.upsert_job(Job(source="hh", source_id="1", url="u", title="Python Backend", company="Acme", description="Python"))
    count = app.score_jobs()
    assert count == 1
    assert app.storage.get_job(job_id).score is not None
```

- [ ] **Step 2: Implement CLI**

Commands: `init`, `sync`, `score`, `list`, `letter`, `status`, `apply-hh`, `report`, `ui`, `mcp`.

- [ ] **Step 3: Run tests and smoke CLI**

Run: `python -m pytest tests/test_services.py -v`
Run: `python -m work_hunter init`

### Task 6: MCP Server

**Files:**
- Create: `work_hunter/mcp_server.py`

- [ ] **Step 1: Implement MCP tools**

Tools: `search_jobs`, `score_jobs`, `list_jobs`, `get_job`, `prepare_cover_letter`, `apply_hh`, `mark_job`, `daily_report`.

- [ ] **Step 2: Smoke import**

Run: `python -c "import work_hunter.mcp_server; print('ok')"`

### Task 7: Local Web UI

**Files:**
- Create: `work_hunter/web/__init__.py`
- Create: `work_hunter/web/server.py`
- Create: `work_hunter/web/static/index.html`
- Create: `work_hunter/web/static/app.css`
- Create: `work_hunter/web/static/app.js`

- [ ] **Step 1: Implement API endpoints**

Endpoints: `GET /api/jobs`, `GET /api/jobs/<id>`, `POST /api/sync`, `POST /api/score`, `POST /api/jobs/<id>/status`, `POST /api/jobs/<id>/letter`, `GET /api/report`, `GET/POST /api/config`.

- [ ] **Step 2: Implement UI**

Inbox table, detail panel, source sync buttons, scoring reasons, letter draft, status actions.

- [ ] **Step 3: Run local server**

Run: `python -m work_hunter ui --port 8787`

### Task 8: Verification

**Files:**
- Modify as needed based on test results.

- [ ] **Step 1: Run full tests**

Run: `python -m pytest -v`

- [ ] **Step 2: Run CLI smoke**

Run: `python -m work_hunter list --limit 5`

- [ ] **Step 3: Start UI**

Run: `python -m work_hunter ui --port 8787`

- [ ] **Step 4: Confirm no secrets leak**

Check UI/API config responses mask `token`, `password`, `secret`, and `api_key`.
