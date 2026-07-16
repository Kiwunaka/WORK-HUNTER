# Task 14 implementation report

## Result

- Added one cached, injectable `WorkHunter` component factory with a configured local fallback built on the existing autopilot engine, executor, repository, recovery sweep, and scheduler.
- Added thin tick, recovery, status, history, and challenge-list facades.
- Routed confirmed single applies, stored apply plans, campaigns, and apply-from-file calls through exact `LiteralConfirmation` one-shot targets and the common engine. Campaign targets are frozen in one authorization before dispatch.
- Removed the direct live `HHApplyClient.apply()` call from `WorkHunter`; dry-run planning remains read-only.
- Added masked read-only MCP tools for autopilot status, history, and challenges. No MCP grant, run, control, or challenge-resolution tool was added; existing agent live-apply blocking remains unchanged.

## Compact TDD evidence

Initial four-test slice:

```text
4 failed
```

The failures were the intended missing constructor injection, facade methods, unified manual path, and MCP views. After implementation:

```text
4 passed in 1.33s
```

Compatibility and focused Task 14 gate:

```text
36 passed in 5.13s
85 passed in 5.44s
```

The trailing Windows pytest temp-directory cleanup `PermissionError` occurred after successful exit, as in earlier tasks.

## Static and caller checks

```text
ruff check <changed Python files>
All checks passed!

mypy work_hunter/services.py work_hunter/mcp_server.py --follow-imports=skip
Success: no issues found in 2 source files

rg -n "\.apply_outcome\(" work_hunter
work_hunter/hh_autopilot/executor.py:302: outcome = self.transport.apply_outcome(...)

rg -n "\.submit_grounded_form\(" work_hunter
<no callers>

rg -n "\.apply\(" work_hunter/services.py
<no callers>
```
