# Work Hunter

Personal local job-search command center for finding vacancies, scoring fit, preparing applications, and running a guarded HH agent flow.

This repository is a **private local-first tool**, not a public SaaS product. It is built around one user, local secrets, local SQLite state, and explicit safety gates for real job applications.

## What This Is

Work Hunter helps with the full job-search loop:

- collect vacancies from HH, Habr, GeekJob, Telegram, Getmatch, Relocate.me, and other public boards;
- score jobs against a local profile;
- draft cover letters, resume tips, ATS summaries, interview prep, and fit analysis;
- operate HH flows: auth status, resumes, negotiations, campaigns, API lab, apply plans, approvals, events, templates, and blacklist;
- plan guarded campaigns with daily caps, kill switch checks, replay events, and manual confirmation before real HH apply;
- prepare external-source application handoffs through Browser Lab dry-run and manual-submit gates;
- continue after apply with pipeline status, follow-up suggestions, calendar hooks, and interview prep packs;
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
pip install -e ".[dev,browser]"
python -m playwright install chromium
python -m work_hunter init
python -m work_hunter config --json
```

Use `pip install -e .` only for the minimal CLI/API surface without browser automation or test tools.

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
python -m work_hunter score
python -m work_hunter list --limit 20 --min-score 50
```

Run the local UI:

```powershell
python -m work_hunter ui --host 127.0.0.1 --port 8787
```

Run the MCP server:

```powershell
python -m work_hunter mcp
```

Inspect source capabilities:

```powershell
python -m work_hunter source-capabilities
```

Inspect external apply certification gates:

```powershell
python -m work_hunter source certification-matrix --level 5
python -m work_hunter source certification-audit hirehi --level 5
python -m work_hunter source external-apply-target hirehi --session hirehi --url https://hirehi.example/apply --method POST --payload-template '{"jobId":"{source_id}"}'
python -m work_hunter source external-apply-from-har getmatch .\session.har --host getmatch.ru
python -m work_hunter source certification-evidence hirehi --level 5 --evidence '{"tests":{"status":"passed","command":"pytest hirehi"}}'
python -m work_hunter source redaction-scan hirehi --payload '{"headers":{"Authorization":"Bearer ..."}}' --text 'client_secret=...'
```

Promote a source only after its evidence package is complete:

```powershell
python -m work_hunter source certify hirehi --level 5 --evidence '{"tests":{"status":"passed","command":"pytest hirehi"}}'
```

Promotion stays blocked until the source has session, URL, tests, replay, redaction, and dry-run evidence. The same matrix is available in the Sources UI, `POST /api/sources/certification-matrix`, and the MCP `source_certification_matrix` tool. Incremental target configuration is available through `POST /api/sources/{source}/external-apply-target` and the MCP `source_external_apply_target` tool; HAR-to-target configuration is available through `POST /api/sources/{source}/external-apply-from-har` and the MCP `source_external_apply_from_har` tool, or directly from Browser Lab import with `configure_external_apply`; evidence recording is available through `POST /api/sources/{source}/certification-evidence` and the MCP `source_certification_evidence` tool; source-specific redaction scans are available through `POST /api/sources/{source}/redaction-scan` and the MCP `source_redaction_scan` tool; final gated promotion is available through `POST /api/sources/{source}/certify` and the MCP `source_certify` tool.

GeekJob, Habr, and generic public-board external pages can detect known application forms for Browser Lab preview and dry-run fallback. Detection never promotes maturity by itself: tests, replay, redaction, dry-run evidence, session, target URL, and certification still gate real apply. Certification audit evidence now carries dry-run host and form signature context when a replay event includes it.

Inspect a post-apply pipeline item from the local API:

```powershell
Invoke-RestMethod http://127.0.0.1:8787/api/pipeline/jobs/1
```

## HH Agent Safety

Real job actions must be treated as sensitive.

- Prefer dry-run, plan, and approval flows first.
- Real HH apply/reply/cleanup requires explicit user intent and an auditable path.
- MCP must not silently send real applications.
- Non-HH real apply requires per-source certification evidence before the source can reach real-submit maturity.
- Never print access tokens, refresh tokens, cookies, client secrets, Telegram bot tokens, SMTP passwords, or full auth headers.
- Unknown forms, tests, captcha/challenge states, duplicate companies, blacklist hits, and suspicious failures should block or escalate.

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

External session mutating calls are intentionally guarded.

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
pytest tests/test_source_adapter_registry.py tests/test_cli_design_surface.py tests/test_mcp_surface.py -q
```

For behavior changes, write or update focused tests first. The project already has good coverage around secret masking, HH safety, agent storage, API lab, scheduler reports, and external session redaction.

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
- external non-HH apply is intentionally held at dry-run/manual-submit handoff unless a source adapter is certified with tests, replay, redaction, and dry-run evidence;
- `init` is richer now, but new WO/FLOW imports should still be previewed with redaction before applying;
- AI readiness, source readiness, and source certification state are visible in the UI, but live provider checks still depend on local runtime configuration.

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
