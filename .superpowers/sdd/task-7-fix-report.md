# Task 7 Fix Report

## Scope

- Fixed the seven Important findings against `f6696f4`.
- Added follow-up regressions for safe same-fence claim of an explicitly
  interrupted cycle, strict validation before resumed provider I/O, and the
  duplicated `NormalizedVacancy.job` facts.
- Did not modify migration `0003`, web UI, later tasks, or unrelated files.
- Did not run the full project suite.

## RED evidence

The inherited regression batch was run before its production fixes:

```text
C:\Users\kiwun\Документы\work hiring\.venv\Scripts\python.exe -m pytest \
  tests/test_hh_autopilot_search.py -q

55 failed, 31 passed
```

The takeover audit added two focused probes and confirmed both failures:

```text
python -m pytest tests/test_hh_autopilot_search.py -q \
  -k "interrupted_cycle_can_be_claimed_under_the_same_current_fence or \
      provider_validates_existing_normalized_evidence_before_remote_io"

2 failed, 89 deselected
```

- `test_interrupted_cycle_can_be_claimed_under_the_same_current_fence` failed
  because the claim path required a replacement fence even after an explicit
  interruption.
- `test_provider_validates_existing_normalized_evidence_before_remote_io`
  reached the forbidden transport call because the cycle-ID helper read only
  `vacancy_id` and bypassed normalized evidence validation.

Three persisted-job probes also failed before their fix:

```text
python -m pytest \
  tests/test_hh_autopilot_search.py::test_persisted_normalized_evidence_is_strictly_reconstructed[job-salary-text] \
  tests/test_hh_autopilot_search.py::test_persisted_normalized_evidence_is_strictly_reconstructed[job-status] \
  tests/test_hh_autopilot_search.py::test_persisted_normalized_evidence_is_strictly_reconstructed[job-fetched-at-type] -q

3 failed
```

The public per-collect evidence probe initially failed because
`SearchResult` exposed only the ambiguous legacy `inserted_count` name:

```text
test_duplicate_references_across_presets_and_resumes_share_live_items

1 failed
```

## Finding-to-fix mapping

1. **Immutable cycle mode**
   - The origin run now defines the cycle mode for every protected mutation.
   - Creation, checkpoint creation, page commit, completion, status changes,
     and claims validate origin/owner account, policy, fence, and mode
     provenance.
   - Shadow cycles cannot be claimed or promoted to live; a later live run
     creates a new cycle.

2. **Hard-crash adoption**
   - An authorized recovery run can atomically adopt an interrupted cycle or a
     stale running live cycle after lease replacement.
   - A still-running cycle requires a different current fence; an explicitly
     interrupted cycle may be claimed under the same still-current fence.
   - The old nonterminal owner run is marked interrupted in the claim
     transaction, while terminal runs remain unchanged.
   - Checkpoints retain the first uncommitted page.

3. **Atomic terminal page commit**
   - The provider computes max-page, short-page, reported-total,
     reported-pages, and budget terminal conditions before commit.
   - Result rows, live items, `next_page`, and checkpoint terminal status are
     committed in one transaction.
   - All five terminal crash-window regressions resume with zero remote I/O.

4. **Exact policy-mismatch membership**
   - Reset candidates require exact cycle/account/resume/vacancy membership in
     `hh_autopilot_search_results`, in addition to origin-run,
     pre-dispatch/nonterminal, zero-attempt, and no-active-attempt predicates.
   - A same-origin nonmember keeps state, score, evidence, version, and journal.

5. **Transactional distinct-ID budget**
   - Every page commit recomputes the validated cycle-wide distinct set under
     `BEGIN IMMEDIATE`.
   - Existing IDs may add a search reference without consuming the cap; new IDs
     over the cap create neither a search result nor a live item.
   - The transaction returns accepted IDs, inserted reference count, new
     distinct count, cycle distinct count, and the committed checkpoint.
   - `SearchResult.inserted_reference_count` now exposes the reference count
     unambiguously while preserving `inserted_count` compatibility.
   - In the real two-connection race both checkpoint commits completed, but
     exactly one reference, one new distinct ID, and one live item were
     accepted. Cap and terminal writes also rolled back with a forced item
     insert failure.

6. **One running cycle per owner**
   - Claim checks for another running cycle owned by the recovery run inside the
     same immediate transaction.
   - Sequential and two-connection concurrent claims produce exactly one
     running owned cycle; the loser receives `StaleWrite`.

7. **Fail-closed facts and evidence**
   - Optional archive/test/application booleans are tri-state and accept only
     exact booleans when present.
   - Salary amounts accept only nonnegative exact integers; salary gross accepts
     only an exact boolean.
   - Unknown archive/schedule facts remain unknown, including `Job.remote`.
   - Persisted normalized JSON rejects nonfinite values, noncanonical encoding,
     missing/extra fields, raw markup, NUL, type coercions, mismatched vacancy
     IDs, and malformed duplicated `job` facts.
   - `job.salary_text` is derived from salary facts, `job.status` must remain
     `new`, and `job.fetched_at` must be canonical UTC seconds.
   - Provider cycle-ID reads, transactional budget reads, and policy-reset
     membership reads validate every normalized result before using it.

## GREEN evidence

Focused takeover regressions:

```text
5 passed in 0.56s
1 passed in 0.21s
```

Complete search regression file:

```text
91 passed in 5.85s
```

Required search/transport/source suite:

```text
C:\Users\kiwun\Документы\work hiring\.venv\Scripts\python.exe -m pytest \
  tests/test_hh_autopilot_search.py tests/test_hh_transport.py \
  tests/test_sources.py -q

133 passed in 5.47s
```

Repository and authorization regression:

```text
C:\Users\kiwun\Документы\work hiring\.venv\Scripts\python.exe -m pytest \
  tests/test_hh_autopilot_repository.py \
  tests/test_hh_autopilot_authorization.py -q

166 passed in 15.72s
```

Legacy storage and migration compatibility:

```text
C:\Users\kiwun\Документы\work hiring\.venv\Scripts\python.exe -m pytest \
  tests/test_storage.py tests/test_storage_backbone.py \
  tests/test_hh_autopilot_migrations.py -q

77 passed in 4.21s
```

Static verification:

```text
mypy.exe --ignore-missing-imports \
  work_hunter/hh_autopilot/repository.py \
  work_hunter/hh_autopilot/search.py \
  work_hunter/hh_autopilot/types.py

Success: no issues found in 3 source files

ruff.exe check tests/test_hh_autopilot_search.py \
  work_hunter/hh_autopilot/repository.py \
  work_hunter/hh_autopilot/search.py \
  work_hunter/hh_autopilot/types.py

All checks passed!
```

## Caveat

Successful pytest processes still print the known Windows
`pytest-current` cleanup `PermissionError` from an atexit callback after the
test process has already completed with exit code zero.
