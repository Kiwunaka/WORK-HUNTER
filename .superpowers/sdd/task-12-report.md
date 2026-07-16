# Task 12 report

Implemented one `HHAutopilot.run(RunRequest) -> RunReport` coordinator for
live, literal, shadow, retry, recovery, and canary runs.

Core behavior:

- fenced lease/run lifecycle with live grant, literal confirmation, or grantless shadow scope;
- stale reconciliation and due retries before discovery;
- existing search, hard-filter, deterministic/AI ranking, repository journal, executor, and reconciler stages are reused;
- stable ready ordering, stop/pause checks between items, and delay only after an actual POST;
- exact one-vacancy canary scope and read-only shadow results;
- CAPTCHA/manual and internal-error outcomes remain isolated per vacancy.

TDD evidence:

- RED: `tests/test_hh_autopilot_engine.py` failed at collection because
  `work_hunter.hh_autopilot.engine` did not exist.
- GREEN: `6 passed in 0.45s`.

Verification:

- Task 12 Step 8 core suite: `537` tests, exit code `0`.
- Ruff on changed production/tests: clean.
- Mypy on changed production files with imports skipped: clean.
- Direct global Mypy additionally reports only the existing missing
  `requests` stubs and two pre-existing `config.py` errors.
- `git diff --check`: clean (Git only reports the existing Windows LF/CRLF notice).
- Known post-exit Windows `pytest-current` cleanup `PermissionError` remains non-fatal.
