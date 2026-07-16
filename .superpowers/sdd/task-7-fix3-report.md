# Task 7 Fix 3 Report

## Scope

- Fixed the final three Important findings against `3e0203e`.
- Did not modify migrations `0003` or `0004`, UI, or later-task code.
- Did not run the full project suite.

## RED evidence

The exact regressions were added before production changes:

```text
python -m pytest tests/test_hh_autopilot_search.py -q \
  -k "<fix3 provenance and collection regressions>"

13 failed, 1 passed, 123 deselected in 1.45s
```

The failures proved that:

- a second recovery claim accepted a recovered live owner tampered to
  `trigger='shadow', grant_id=NULL`;
- run account/policy BLOB and noncanonical text were accepted, while BLOB
  trigger/status were coerced and raised the wrong exception;
- a BLOB policy `b"hash"` could match the crafted text policy `"b'hash'"`;
- malformed collection elements after entry 100 were ignored;
- the old entry-slice cap could persist fewer than 100 unique values after
  deduplication.

The one passing test was the positive control proving valid multi-recovery
claims already worked and had to remain compatible.

## Finding-to-fix mapping

1. **Owner requirements derive from immutable origin mode**
   - `_assert_search_owner_provenance_for_update()` now derives live/shadow
     requirements from the cycle origin.
   - A shadow cycle owner must remain the exact shadow origin and grantless.
   - A live cycle rejects every shadow owner; a recovered owner must retain the
     `recovery` trigger and an exact historical applications grant.
   - The two-recovery tamper regression proves claim, cycle status, item count,
     and journal count remain unchanged on rejection.
   - A valid two-recovery control reaches claim version 2.

2. **Strict non-coercing run provenance**
   - Run ID, grant ID, and fencing token use strict persisted integer readers.
   - Account ID uses canonical strict persisted text.
   - Trigger, status, and policy hash use exact persisted text; trigger/status
     allowlist failures now raise `StaleWrite`.
   - BLOB and noncanonical account/policy values fail before provenance
     comparison, including the crafted BLOB-to-string policy collision.

3. **Validate all collection entries and store at most 100 unique values**
   - Identifier and relation parsers traverse the entire list/tuple.
   - Every tail element is type/content validated even after the storage cap.
   - Only the first 100 unique cleaned values are retained, so early
     duplicates do not waste the cap and valid oversized inputs remain
     deterministic and bounded.
   - Regressions cover the malformed 101st relation, work format,
     professional role, and key skill.

## GREEN evidence

Focused fix regressions:

```text
14 passed, 123 deselected in 0.77s
```

Complete search regression file:

```text
137 passed in 7.94s
```

Required search, transport, source, and migration suite:

```text
224 passed in 12.72s
```

Repository and authorization regression:

```text
166 passed in 16.82s
```

Storage, backbone, and migration regression:

```text
78 passed in 4.52s
```

Focused legacy item and migration-from-0003 compatibility:

```text
2 passed, 83 deselected in 0.21s
1 passed in 0.35s
```

Static verification:

```text
mypy --ignore-missing-imports \
  work_hunter/hh_autopilot/repository.py \
  work_hunter/hh_autopilot/search.py

Success: no issues found in 2 source files

ruff check tests/test_hh_autopilot_search.py \
  work_hunter/hh_autopilot/repository.py \
  work_hunter/hh_autopilot/search.py

All checks passed!

git diff --check
clean (only expected Windows LF-to-CRLF warnings)
```

## Caveat

Successful pytest processes still print the known Windows
`pytest-current` cleanup `PermissionError` from an atexit callback after the
test process exits with code zero.
