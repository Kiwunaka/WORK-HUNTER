# Changelog

## Unreleased - 2026-07-13

### HH native challenge resolution

- Submit current HH `vacancyTests` through the native `task_*` web payload with configurable AI answers.
- Fill supported redirect forms grounded-first, with configurable AI completion for remaining fields.
- Solve application CAPTCHA through a Vision-capable OpenAI-compatible model in the persisted HH browser session, synchronize cookies back to the API transport, retry the original application, and retain manual fallback.

### Apple HIG cockpit redesign

- Replace the legacy navigation with eight canonical destinations and a deterministic **Today** dashboard.
- Add a three-step first-run onboarding flow with safe deferral, reload recovery, and profile-scoped resume activation.
- Add contextual coach marks, accessible notifications, sheets, popovers, inline failures, and empty states.
- Add full, compact, and mobile responsive shell modes with local SVG icons and reduced-motion support.
- Group Applications, Analytics, and Settings into URL-synchronized tabs while preserving existing workflows.

### Safety and compatibility

- Replace browser alerts and confirms with typed feedback and live-action safety sheets.
- Revalidate HH mutations immediately before execution and retain literal-boolean server confirmation guards.
- Preserve legacy deep links through canonical URL replacement and package all local UI controller assets.

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
