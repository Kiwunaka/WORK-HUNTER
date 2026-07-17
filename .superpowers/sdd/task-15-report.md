# Task 15 implementation report

## Result

- Added the complete `hh autopilot` CLI family: validate, enable/disable,
  status, shadow, canary, run/recover, pause/resume/stop, kill/clear, retry,
  challenges/resolution, and history.
- Added browser-assisted `hh auth` login, explicit cookie import, logout,
  per-account refresh/status, and confirmed profile selection while preserving
  the existing token/OAuth and legacy CLI routes.
- Mutating and live-run commands require an explicit account or explicit
  all/global scope; there is no first-account fallback. IDs and history limits
  are positive and challenge actions are closed choices.
- Added thin `WorkHunter` controls over the existing authorizer, engine,
  repository, recovery, HH client, and browser authorizer. CLI output is masked
  JSON; login remains visible/headful for user password, OTP, and CAPTCHA work.

## Compact TDD evidence

Initial CLI contract run:

```text
25 failed, 17 passed
```

Failures were the intended missing autopilot/auth parsers, routes, and explicit
OAuth client-configuration response. The final CLI/compatibility slice passed:

```text
42 passed
```

The test file has five parameterized test functions and does not instantiate or
launch a browser.

## Required Step 8 gate

```text
71 passed in 3.72s
```

The known Windows pytest temp cleanup `PermissionError` occurred after a
successful exit and did not affect the test result.

## Static checks

```text
ruff check <changed Python files>
All checks passed!

mypy work_hunter/cli.py work_hunter/services.py --ignore-missing-imports --follow-imports=skip
Success: no issues found in 2 source files

git diff --check
clean (line-ending notices only)
```
