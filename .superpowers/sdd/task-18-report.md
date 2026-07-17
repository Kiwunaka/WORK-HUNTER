# Task 18 report

## Outcome

Shipped the HH Autopilot operator surface for installation, configuration, rollout, scheduling, recovery and release evidence.

The guide states the current boundary plainly: CAPTCHA uses manual browser handoff with cookie continuity and no solver. Deterministic fake-HH/browser fixtures do not prove the current live HH flow.

## Changed files

- `docs/hh-autopilot.md` — goal-first quick start and all 15 required operator sections.
- `README.md` — concise Autopilot quick start, defaults and operator-guide link.
- `.github/workflows/ci.yml` — deterministic suite, compact named restart/concurrency gate and browser fixture gate without another matrix.
- `Dockerfile` — runtime wheel installs the `ui` extra; existing migrations/static, non-root UID/GID and `/data` volume remain intact.
- `tests/test_package_contract.py` — three contracts for packaged resources, fresh disabled state and CLI/help/masked export.

## Documented contracts

- Full `sources.hh.autopilot` defaults, bounds, enums and cross-field constraints.
- `hh_campaign_presets` validation and a multi-account/multi-resume example.
- Browser/token/cookie auth and user-supplied OAuth requirement.
- Validate -> shadow -> named canary -> per-run one -> normal rollout.
- Every Autopilot CLI command and equivalent loopback UI control.
- Windows Task Scheduler and Linux cron commands using `examples/hh-autopilot-runner.json`.
- Quotas, delays, schedule windows, cooldowns, retry, lease takeover and recovery.
- Grounded forms, unknown-data skip, manual assessment and manual CAPTCHA lifecycle.
- Ambiguous delivery classification and held-quota consequences.
- Pause/disable/stop/kill-switch semantics.
- Secret masking, private browser state, honest retention limitation and backup/restore.
- Every stable application outcome group and focused troubleshooting commands.
- Deterministic-vs-live evidence and parity-maintenance exclusions.

`allow_broad_apply` is now explicitly documented as legacy state that cannot authorize an application.

## Verification

```text
python -m pytest tests/test_package_contract.py -q
3 passed

python -m pytest tests/test_release_artifact.py -k "dockerfile... or optional_browser... or dockerignore..." -q
3 passed, 5 deselected

ruff check tests/test_package_contract.py
All checks passed

ruff format --check tests/test_package_contract.py
1 file already formatted

mypy tests/test_package_contract.py --ignore-missing-imports --follow-imports=skip --no-incremental
Success: no issues found in 1 source file

git diff --check
passed
```

The focused pytest run emitted a Windows temp-directory cleanup warning after pytest returned exit code 0. Test assertions passed.

A mypy probe with imported project modules exposed existing errors in `work_hunter/hh_autopilot/config.py:1620,1623`; Task 18 does not modify that module. Per task instruction, no full suite, build or audit was run here.

## Release statement

No live HH canary was run. Current evidence supports:

> deterministic contract verified; current live HH application flow not proven
