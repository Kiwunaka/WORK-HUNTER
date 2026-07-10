# Changelog

## 1.0.0 - 2026-07-10

### Safety

- Require literal confirmation for every HH mutation, including API Lab and resume operations.
- Reject cross-origin, non-JSON, DNS-rebinding, and non-loopback cockpit mutations.
- Preserve masked configuration secrets during UI updates.

### Data correctness

- Persist OAuth token rotation atomically and isolate scores by active profile.
- Enforce SQLite foreign keys and atomic migrations.
- Preserve query-based vacancy identity and honor ghost-job age.

### Browser cockpit

- Preserve resume fields, target ghost rows explicitly, and default agent preflight to dry mode.
- Add keyboard operation, resilient loading, safe rendering, and offline assets.

### Packaging and verification

- Package UI and SQL resources in the 1.0.0 wheel.
- Add a non-root wheel-based Docker build definition; final image build and safe smoke remain pending release verification.
