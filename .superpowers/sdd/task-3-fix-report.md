# Task 3 Fix Report: Account-Aware History Hardening

## Status

Fixed the three Important review findings without expanding Task 3 scope:

- application account/resume identities now use the Task 2 `strip().casefold()`
  canonical form at save, exact-read, reassignment, migration, and startup-profile
  boundaries;
- CSV application exports include both identity columns;
- the packaged migration creates the newest-per-job read index while retaining the
  account/sent index.

## RED

Command:

```powershell
& '..\..\.venv\Scripts\python.exe' -m pytest tests\test_hh_autopilot_migrations.py -q
```

Result: `11 failed, 26 passed`.

Failure breakdown:

- normalization/startup safety: 8 failures;
- CSV identity export: 1 failure;
- packaged index/query plan: 2 failures.

The raw-key collision characterization already passed before production changes
because the old implementation preserved case. It remained in the suite to prove
that canonicalization does not accidentally turn two credential-bearing raw keys
into evidence of one account.

## GREEN

Implementation details:

- one storage canonicalizer handles account and resume identifiers; account IDs
  remain non-empty, while the compatibility resume ID `""` remains valid;
- non-string resume IDs are rejected by both save and exact-read paths;
- `LEGACY` canonicalizes to `legacy`, so sentinel list/report readers see it;
- the legacy SQL rebuild calls the registered Python canonicalizer, preserving true
  `casefold()` behavior rather than SQLite ASCII-only `lower()` behavior;
- startup keeps each credential-bearing named raw profile as separate evidence,
  even when multiple raw keys canonicalize to the same profile ID;
- legacy `sources.hh` and named `default` are deduplicated only when they share
  non-conflicting user-auth evidence; conflicting evidence leaves the sentinel;
- `idx_applications_job_latest(job_id, updated_at DESC, applied_at DESC, id DESC)`
  satisfies the aggregate newest-read order without a temporary sort.

## Tests

Focused final Task 3 suite:

```powershell
& '..\..\.venv\Scripts\python.exe' -m pytest tests\test_hh_autopilot_migrations.py -q --basetemp=C:\Users\kiwun\AppData\Local\Temp\work-hunter-task3-fix-final-focused-2
```

Result: `43 passed in 2.85s`.

Related migration/storage/services suite:

```powershell
& '..\..\.venv\Scripts\python.exe' -m pytest tests\test_hh_autopilot_migrations.py tests\test_storage.py tests\test_storage_backbone.py tests\test_services.py -q --basetemp=C:\Users\kiwun\AppData\Local\Temp\work-hunter-task3-fix-related
```

Result: `99 passed in 4.83s`.

Account-profile and packaged-release smoke:

```powershell
& '..\..\.venv\Scripts\python.exe' -m pytest tests\test_hh_account_profiles.py tests\test_release_artifact.py -q --basetemp=C:\Users\kiwun\AppData\Local\Temp\work-hunter-task3-fix-packaging
```

Result: `11 passed in 19.10s`.

The single requested full-suite run:

```powershell
& '..\..\.venv\Scripts\python.exe' -m pytest -q --basetemp=C:\Users\kiwun\AppData\Local\Temp\work-hunter-task3-fix-full
```

Result: `832 passed, 3 skipped in 271.59s`.

The explicit non-string type-boundary parameter cases were requested while that
full run was in progress. They changed tests only, not production code, and the
final focused run above includes them.

## Commit

`fix: harden account-aware application history` (this commit)

## Concerns

No functional concern remains. The migration SQL depends on the deterministic
`canonicalize_application_identity` SQLite function registered by `Storage`, which
is the application's migration runner and is covered by source-tree and installed-
wheel smoke tests.
