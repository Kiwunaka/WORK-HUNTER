# Work Hunter

Personal local job-search command center for finding vacancies, scoring fit, preparing applications, and sending applications across HH, LinkedIn, and external job boards.

This repository is a **private local-first tool**, not a public SaaS product. It is built around one user, local secrets, local SQLite state, and explicit safety gates for real job applications.

## What This Is

Work Hunter helps with the full job-search loop:

- collect vacancies from HH, LinkedIn, Indeed, Habr, GeekJob, Telegram, GetMatch, Relocate.me, HireHi, CareerSpace, Another-IT, and Jabka;
- score jobs against a local profile;
- draft cover letters, resume tips, ATS summaries, interview prep, and fit analysis;
- operate HH flows: auth, resumes, campaigns, tests/forms, negotiations, Chatik button replies, API lab, approvals, events, cleanup, and blacklist;
- send non-HH applications through a persistent Playwright profile or a promoted authenticated HAR/session adapter;
- fill application questions from explicit answers, candidate facts, and the configured AI backend;
- expose a local web cockpit and MCP tools for agent-driven workflows;
- research personal API/session adapters from HAR files without printing secrets.

The guiding product idea: the market is noisy, ATS/AI filters are brutal, and manual job search burns time. Work Hunter is the private automation layer that helps the candidate fight back while keeping risky actions auditable.

## Repository Map

| Path | Purpose |
|---|---|
| `work_hunter/cli.py` | CLI entrypoint and command routing. |
| `work_hunter/services.py` | Main application service facade. Most workflows pass through `WorkHunter`. |
| `work_hunter/storage.py` | SQLite schema and persistence methods. |
| `work_hunter/sources/` | Vacancy source adapters. |
| `work_hunter/hh_transport/` | HH API/browser/cookie/XSRF transport helpers. |
| `work_hunter/hh_agent/` | HH agent policy, approvals, research, Telegram, events, forms, MCP handlers. |
| `work_hunter/llm/` and `work_hunter/ai_backends.py` | Provider-neutral LLM helpers and OpenCode/direct backend support. |
| `work_hunter/web/` | Local HTTP server and static UI. |
| `tests/` | Pytest suite. Treat tests as the executable contract. |
| `docs/` | Plans, safety notes, API recon notes, and historical implementation context. |
| `.opencode/agents/work-hunter-ai.md` | Safe OpenCode agent prompt for local AI drafting/analysis. |

## Do Not Commit

These are intentionally ignored and must stay local:

- `.work-hunter/`: config, SQLite DB, HH local state, logs, tokens, cookies;
- `external/`, `.codex_compare/`, `.compare-forks/`, `.compare-hh-applicant-tool/`: donor and research repos;
- `.venv/`, `.pytest_cache/`, `__pycache__/`, `work_hunter.egg-info/`;
- `.research/`, `.playwright-mcp/`, HAR/session/cookie dumps;
- any `*.db`, `*.sqlite3`, `*.log`, `*.har`, `*.pem`, `*.key`, `.env*`.

Before committing, run a staged-only secret/path check.

```powershell
git diff --cached --name-only | rg "^(\.work-hunter/|external/|\.venv/|\.research/|\.codex_compare/|\.compare-forks/|\.compare-hh-applicant-tool/|\.playwright-mcp/|.*\.(db|sqlite|sqlite3|log|har|pem|key|p12|pfx)$)"
git grep --cached -n -I -E "(-----BEGIN [A-Z ]*PRIVATE KEY-----|sk-[A-Za-z0-9_-]{20,}|sk-or-v1-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9_]{20,})"
```

No output is the desired result.

## Quick Start

Use Python 3.11+.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[browser,ui]"
python -m playwright install chromium
python -m work_hunter init
python -m work_hunter doctor --json
python -m work_hunter config --json
```

### HH Autopilot

Полный операторский гайд: [docs/hh-autopilot.md](docs/hh-autopilot.md). Сверка
с `hh-applicant-tool 1.8.26` и `hh-ai-responder 0.2.4`:
[docs/hh-reference-parity.md](docs/hh-reference-parity.md). Полный аудит доноров
и форков от 30 августа 2026 года:
[docs/2026-08-30-upstream-audit.md](docs/2026-08-30-upstream-audit.md).

```powershell
python -m pip install -e ".[browser,ui]"
python -m playwright install chromium
work-hunter hh auth login --account default
work-hunter hh autopilot validate --account default
work-hunter hh autopilot shadow --account default
work-hunter hh autopilot canary --account default --resume RESUME_ID --vacancy VACANCY_ID --confirm
work-hunter hh autopilot enable --account default --confirm
work-hunter runner --plan examples/hh-autopilot-runner.json
```

Defaults are validated and configurable: `50/day`, `10/run`, `45-120s`, `08:00-21:00 Europe/Moscow`, hourly, and up to `20 x 100` search results. Fresh installs remain disabled with no grant.

## Common Commands

Initialize local config:

```powershell
python -m work_hunter init
```

Check HH auth state without printing tokens:

```powershell
python -m work_hunter hh-auth-status
```

Sync, score, and list jobs:

```powershell
python -m work_hunter sync --source habr --limit 20
python -m work_hunter sync --source linkedin --limit 20
python -m work_hunter sync --source indeed --limit 20
python -m work_hunter score
python -m work_hunter list --limit 20 --min-score 50
python -m work_hunter apply-plan 123
python -m work_hunter apply 123 --letter-file .\letter.txt --confirm
python -m work_hunter browser-login linkedin
python -m work_hunter browser-login indeed
```

Run the local UI:

```powershell
python -m work_hunter ui --host 127.0.0.1 --port 8787
```

### Local UI

The Apple HIG-inspired cockpit has eight destinations:

- **Today** — readiness, up to three priority actions, fresh matches, upcoming events, and pending decisions;
- **Vacancies** — search, filters, scoring, saved items, vacancy details, letters, and one apply flow for every supported source;
- **Applications** — pipeline, agent approvals/runs, and automation tools;
- **Calendar** — interviews, follow-ups, reminders, and tasks;
- **Assistant** — job-aware chat and drafting;
- **Analytics** — funnel, score distribution, source performance, and trends;
- **Sources** — connection health, last sync, errors, and retry;
- **Settings** — profiles, resumes, searches, HH/AI configuration, appearance, help, and advanced API Lab tools.

On a genuine first run, a three-step sheet reviews the search goal, enabled sources, and active resume. **Set up later** defers it only for the current browser tab and does not pretend setup is complete. Progress survives reloads, including the case where a resume was created but activation must be retried.

Use **Settings → Help → Repeat introduction** to review setup again. **Show tips again** resets contextual coach marks. The interface adapts from a full sidebar to a compact icon sidebar and then to a mobile menu sheet.

Autonomous HH applications require an active account- and policy-bound HH Autopilot grant created by explicit `enable --confirm`. The legacy `sources.hh.allow_broad_apply` flag is ignored for authorization. UI actions that create, revoke, or replace authority use a dedicated confirmation sheet and literal JSON `confirm: true`; server-side checks remain authoritative.

Run the MCP server:

```powershell
python -m work_hunter mcp
```

Inspect source capabilities:

```powershell
python -m work_hunter source-capabilities
```

## Application Execution

Real job actions must be treated as sensitive.

- The same plan/confirm contract is used for HH, LinkedIn, and external boards.
- A literal confirmation sends the application; browser and session transports are first-class execution paths.
- MCP exposes `apply_job` and can send when `confirm=true` is passed literally.
- Never print access tokens, refresh tokens, cookies, client secrets, Telegram bot tokens, SMTP passwords, or full auth headers.
- HH vacancy tests, supported forms, and application CAPTCHA resolve automatically when AI/browser settings are present; exhausted or unknown challenge states escalate per vacancy.

Relevant tests:

```powershell
pytest tests/test_mcp_safety.py tests/test_hh_agent_mcp.py -q
pytest tests/test_hh_agent_approval.py tests/test_hh_agent_research.py -q
```

## API Recon And External Sessions

The API recon tools are for the owner’s own accounts and sessions. They should redact secrets in reports.

```powershell
python -m work_hunter api-discover-url https://example.com --host example.com
python -m work_hunter api-probe-url https://example.com --host example.com --limit 20
python -m work_hunter api-recon-har .\session.har --host example.com
python -m work_hunter external-adapter-plan .\session.har --source example --host example.com
```

Raw external-session calls remain a low-level laboratory interface. Normal applications do not require `--unsafe-lab`; the confirmed `apply` flow invokes the configured adapter directly.

```powershell
python -m work_hunter external-session call example POST https://example.com/api/apply --data-file .\payload.json --real --unsafe-lab
```

Use this only when the user explicitly asks for a real personal-account lab call.

## Tests

Run the full suite:

```powershell
pytest -q
```

Useful targeted suites:

```powershell
pytest tests/test_config.py tests/test_ai_backend.py -q
pytest tests/test_sources.py tests/test_public_boards.py -q
pytest tests/test_hh_auth.py tests/test_hh_transport.py -q
pytest tests/test_external_sessions.py tests/test_api_recon.py -q
```

For behavior changes, write or update focused tests first. The project already has good coverage around secret masking, HH safety, agent storage, API lab, scheduler reports, and external session redaction.

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

The cockpit is loopback-only. Real HH mutations require a literal confirmation flag. Strings such as `"true"` do not authorize them.

Release verification does not read or copy file contents or secrets from .work-hunter; product smokes use isolated roots.

## Current Agent Notes

Start with these files before making large changes:

1. `README.md`
2. `docs/superpowers/specs/2026-04-26-work-hunter-design.md`
3. `docs/superpowers/plans/2026-06-09-ultimate-hh-tool-harvest-plan.md`
4. `docs/superpowers/plans/2026-06-10-external-api-parity.md`
5. `work_hunter/services.py`
6. relevant tests under `tests/`

Known important gaps to verify before building on top:

- some MCP HH agent paths are still plan/dry-run oriented and should be checked against tests before claiming true end-to-end automation;
- AI backend support exists for direct OpenAI-compatible HTTP and OpenCode, but provider routing needs a clearer registry before adding Codex/OpenCode subscription runtime support;
- UI source status currently needs richer capability/status aggregation.

## Git Workflow

This repository is private. Keep it that way.

Recommended baseline flow:

```powershell
git status --short --ignored
git diff --stat
pytest -q
git add <explicit files>
git diff --cached --check
git commit -m "<short change summary>"
git push
```

Avoid `git add -A` unless you have inspected ignored and untracked paths. The local workspace contains intentionally private runtime data.

## License And Donor Code

Licensed upstream/fork research exists locally but is not committed in the initial baseline. If future work ports substantial donor code, preserve attribution in docs or commit messages and never copy donor secrets, personal prompts, or machine-specific scripts as defaults.
