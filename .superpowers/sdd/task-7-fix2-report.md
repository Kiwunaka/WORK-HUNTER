# Task 7 Fix 2 Report

## Scope

- Fixed all seven Important findings against `5b0fd95`.
- Added forward migration
  `0004_hh_autopilot_search_budget.sql`; released migration `0003` was not
  modified.
- Did not modify UI or later-task implementation and did not run the full
  project suite.

## RED evidence

The complete inherited regression batch was added before production changes:

```text
python -m pytest tests/test_hh_autopilot_search.py \
  tests/test_hh_autopilot_migrations.py -q -k "<fix2 regressions>"

30 failed, 2 passed, 135 deselected in 3.23s
```

The two passing cases were positive compatibility controls: valid URL/list
facts and a valid inactive historical grant. The 30 failures proved:

- no durable cycle cap or `0004` upgrade existed;
- a conflicting repeated reference was returned as accepted;
- malformed URL/relation/list facts were silently treated as absent;
- count helpers bypassed normalized evidence validation;
- resume identity was not canonical across search and item creation;
- claim stranded a sibling running cycle on the old owner;
- tampered historical grants were ignored.

A later adversarial audit found that treating a direct commit without a cap as
unlimited would broaden authority. Its regression was also captured before the
fix:

```text
test_direct_page_commit_requires_initialized_or_exact_durable_cap

1 failed
Failed: DID NOT RAISE StaleWrite
```

## Finding-to-fix mapping

1. **Durable nonexpandable distinct cap**
   - Migration `0004` adds nullable, nonnegative, exact-integer
     `distinct_vacancy_cap`.
   - `initialize_search_cycle_distinct_cap()` initializes or tightens the cap
     under `BEGIN IMMEDIATE` after strict evidence and runtime authorization
     checks.
   - Stale/larger proposals cannot increase the cap; recovery preserves it.
   - Every page commit enforces the stored cap. A direct commit fails closed
     unless the cap is already initialized or an exact cap is supplied.
   - Zero-budget collect initializes cap `0` with no remote I/O.
   - Sequential presets, recovery, malformed storage, rollback, and real
     two-connection races are covered.

2. **Accepted IDs require a newly inserted reference**
   - `accepted_vacancy_ids` is appended only after a successful result-row
     insert.
   - Conflict rows are strictly reconstructed and provenance-checked, then
     omitted from accepted IDs and counters.
   - A new preset/resume reference to an existing cycle-wide vacancy remains
     accepted while consuming zero distinct budget.

3. **Strict URL/relation/list facts**
   - Present HH URL facts require exact string or null.
   - Relations require list/tuple entries that are exact strings or mappings
     with an exact string `id`.
   - Work formats, professional roles, and key skills use the same fail-closed
     collection parsing; malformed entries reject the vacancy before
     persistence.

4. **Strict search-result counts**
   - Cycle, origin-run, and unfiltered counts now read and reconstruct every
     selected normalized result before returning `len`.
   - Existing ID/cap/reset/pre-I/O helpers continue to share the strict
     normalized row reader.

5. **Canonical resume identity**
   - `SearchRequest`, checkpoint APIs/readers, result readers, shadow results,
     and `_create_item_for_update()` all use the same stripped casefolded
     identity.
   - Private item creation verifies the supplied idempotency hash against the
     canonical public formula.

6. **Old-owner sibling cycles**
   - Claim validates every other running cycle on the abandoned owner under
     the same immediate transaction.
   - Any sibling causes `StaleWrite` before target, sibling, or old-run
     mutation; sequential and second-connection regressions prove rollback.

7. **Historical live grant provenance**
   - Every live origin/owner run requires a strict positive `grant_id` and an
     existing historical grant with exact account, applications scope, policy,
     generation, and active storage types.
   - Historical grants may be inactive; the replacement recovery run still
     separately requires the exact current active grant.
   - Null/missing/wrong-account/wrong-policy/wrong-scope/malformed historical
     grants reject claim without mutation.

## GREEN evidence

Focused durable-cap, conflict, migration, race, and rollback checks:

```text
11 passed, 156 deselected in 1.17s
```

Focused capability/count/resume checks:

```text
16 passed, 106 deselected in 0.59s
```

Focused claim/sibling/historical-grant checks:

```text
18 passed, 104 deselected in 1.67s
```

Direct-commit fail-closed regression:

```text
1 passed in 0.35s
```

Complete search regression file:

```text
123 passed in 8.24s
```

Required search/transport/source suite:

```text
165 passed in 8.05s
```

Repository and authorization regression:

```text
166 passed in 16.08s
```

Storage, backbone, and migration regression:

```text
78 passed in 4.20s
```

Focused legacy item and migration compatibility:

```text
3 passed, 82 deselected in 0.27s
1 passed in 0.53s
```

Static verification:

```text
mypy --ignore-missing-imports \
  work_hunter/hh_autopilot/repository.py \
  work_hunter/hh_autopilot/search.py \
  work_hunter/hh_autopilot/types.py

Success: no issues found in 3 source files

ruff check <all changed Python files>

All checks passed!
```

## Caveat

Successful pytest processes still print the known Windows `pytest-current`
cleanup `PermissionError` from an atexit callback after the test process has
already exited with code zero.
