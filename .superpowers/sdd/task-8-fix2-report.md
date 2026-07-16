# Task 8 Fix 2 Report

Base: `e3f5de4 fix: harden HH ranking decisions`

## RED evidence

Production code was unchanged for the initial regressions.

- Policy/ranking regression package: `35 failed, 4 passed in 1.91s`.
- Additional fixed-point credential/markup and PII probes:
  `7 failed in 0.66s`.
- Repository fencing, post-lock expiry, stage-edge, and strict-reader
  regressions: `11 failed, 4 passed in 3.56s`.

## Implemented

- Added one fixed-point sanitizer used by policy facts/evidence, public value
  objects, AI prompt facts, and AI output.
- Added boundary-aware credentials and PII detection without rejecting
  legitimate job words such as `Secretary`, `cookiecutter`, `hotplug`, and
  `proxying`.
- Closed the reason-specific `FilterDecision` evidence matrix.
- Replaced concatenated/raw substring matching with one per-fact Unicode
  phrase matcher.
- Validated all present salary aliases and inverted ranges before neutral or
  disabled-filter shortcuts.
- Canonicalized weights, weighted totals, and signed zero with `math.fsum()`.
- Validated area/relocation aliases and restored absent optional facts for
  `NormalizedVacancy`.
- Grounded and sanitized every nonempty AI evidence and reason string against
  one individual allowlisted fact.
- Made fenced-run tokens mandatory and checked lease expiry after acquiring
  the SQLite write lock.
- Blocked public generic stage-owned transitions and tightened strict item
  reconstruction for `ELIGIBLE`, `RANKED`, and `READY`.

## GREEN evidence

- `tests/test_hh_autopilot_policy.py`: `94 passed`.
- `tests/test_hh_autopilot_ranking.py`: `115 passed`.
- `tests/test_hh_autopilot_repository.py`: `128 passed`.
- Required combined policy/ranking/repository/scoring command:
  `340 passed in 16.00s`.
- Required search/config/state-machine command:
  `302 passed in 11.92s`.

Both required pytest commands exited successfully. On this Windows host pytest
printed a non-fatal temp-directory cleanup `PermissionError` after the passing
summary.

## Static verification

- Ruff on every changed Python file: `All checks passed!`.
- Mypy on the five changed production files with
  `--ignore-missing-imports`: `Success: no issues found in 5 source files`.
  The host Python lacks the optional `types-requests` dev stub, so a plain
  system-Mypy invocation stops in unchanged `work_hunter/ai_backends.py`.
- `git diff --check`: passed.

No migrations, configuration defaults/schema, UI, executor, challenges,
scheduler, or later-task code were changed.
