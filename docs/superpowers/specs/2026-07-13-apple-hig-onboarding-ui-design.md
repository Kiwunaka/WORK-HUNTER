# Work Hunter Apple HIG UI and Onboarding Design

**Date:** 2026-07-13

**Status:** approved for implementation planning

## Goal

Replace the current dense browser cockpit with a clear, desktop-first command center inspired by Apple Human Interface Guidelines. A first-time user should understand what is configured, what requires attention, and what to do next without reading documentation. Existing Work Hunter capabilities and safety guarantees remain available.

The primary post-onboarding destination is a new **Today** screen. The first-run experience is a three-step setup wizard followed by at most four contextual coach marks. Feedback uses a consistent system of sheets, popovers, toasts, inline errors, and empty states instead of browser alerts or unexplained output blocks.

## Product Constraints

- Work Hunter remains a private, local-first web application for one user.
- The existing standard-library HTTP server, static frontend, service facade, SQLite storage, and API routes remain the implementation foundation.
- No UI path may weaken the current literal-boolean confirmation requirements or server-side guards for live HH mutations.
- Runtime UI assets remain local. No remote fonts, scripts, analytics, or executable dependencies are introduced.
- Existing functionality is reorganized, not removed.
- The redesign is desktop-first but must remain usable on a narrow browser viewport.

## Approved Direction

Use a native command-center model rather than a linear wizard-only product or an AI-first shell.

- A translucent, restrained sidebar provides stable global navigation.
- Content uses open space, semantic lists, tables, and a small number of purposeful panels instead of nested card grids.
- The Today screen prioritizes current tasks, fresh matches, upcoming events, and decisions awaiting approval.
- Advanced and dangerous controls appear through progressive disclosure.
- Motion is brief and functional and respects `prefers-reduced-motion`.
- The visual language uses system fonts, Apple-like spacing and hierarchy, quiet neutral surfaces, blue for primary actions, green for completed/safe status, amber for attention, and red only for destructive or live irreversible actions.

## Information Architecture

The persistent navigation contains eight destinations.

| Destination | Purpose | Existing capability mapping |
|---|---|---|
| Today | Daily priorities, fresh matches, upcoming events, readiness, pending approvals | New composition of existing jobs, tasks, events, approvals, resumes, and source state |
| Vacancies | Search results, filters, saved items, vacancy detail, scoring, letters | `/jobs` plus the current Favorites view as a saved filter |
| Applications | Application pipeline, agent queue, approvals, runs, outcomes, ghost follow-up | Relevant `/agent` workflows and application statuses |
| Calendar | Interviews, follow-ups, reminders | `/calendar` |
| Assistant | Job-aware chat, drafting, analysis, preparation | `/chat` and job AI actions |
| Analytics | Funnel, score distribution, sources, trends | `/stats` and `/trends` |
| Sources | Source connectivity, capability, last sync, errors | `/sources` |
| Settings | Profiles, resumes, AI, HH auth, notifications, appearance, help | `/settings`; HH API Lab and low-frequency operator tools live under an Advanced subsection |

Old route URLs continue to resolve. Routes whose content moved redirect to the new canonical destination and preserve the relevant subview where practical: `/favorites` opens the saved Vacancies filter, `/stats` and `/trends` open Analytics, and `/agent` opens Applications. Direct deep links must not silently discard the user's intended context.

## Today Screen

Today answers one question: **What should I do now?** It contains four bounded regions.

1. **Readiness strip** — shows whether the active profile, at least one source, and an active resume are available. Optional HH and AI configuration appear as recommendations, not blockers for public-source search.
2. **In focus** — up to three ordered actions derived from real state, such as reviewing high-scoring matches, approving a prepared application, or preparing for an upcoming interview.
3. **Fresh matches** — the three best unseen or new vacancies with score, role, company, work format, and salary summary.
4. **Upcoming and decisions** — the next two calendar items and a compact entry point for pending agent approvals.

The primary action is **Find vacancies**. The screen must not invent metrics or tasks when data is absent; it uses the defined empty states instead.

## First-Run Onboarding

### Trigger and persistence

The UI derives readiness from loaded application data and stores only presentation progress locally under a versioned key such as `work-hunter:onboarding:v2`.

- If the active profile has no meaningful desired role/query, no source is enabled, or no active resume exists, open the setup sheet at the first incomplete step.
- HH authentication and AI credentials are optional and never prevent completion.
- If the configuration is already ready when the redesigned UI first opens, skip the setup wizard and begin with contextual guidance.
- Wizard progress resumes after reload.
- **Set up later** dismisses the sheet for the current session but leaves a visible readiness action on Today.
- Completing or explicitly skipping a step is recorded separately so partially completed setup is never presented as complete.
- Settings includes **Run introduction again**, which resets presentation progress without deleting product configuration.

### Step 1 — Search goal

Collect and save only the minimum useful profile fields through the existing config update path:

- desired role/search query;
- preferred work format;
- minimum monthly salary.

The screen explains that detailed skills, exclusions, and locations can be edited later.

### Step 2 — Sources and HH

- Show available sources and their current enabled/connection state.
- Allow enabling public sources without credentials.
- Present HH connection as an explicit optional action with an explanation of what authorization enables.
- Do not perform a credentialed HH check until the user requests it.

### Step 3 — Resume and first search

- Select an existing active resume or create a local resume draft using the existing resume workflow.
- Show a readiness summary.
- Finish with **Find first vacancies**, which closes the sheet, starts the ordinary sync/score flow, and routes to Today.
- Failure to sync does not roll back completed settings; show an inline error with Retry.

## Contextual Guidance

After the setup sheet, show no more than four coach marks and no more than one previously unseen coach mark per browser session.

1. Find vacancies on Today.
2. Read the vacancy match score.
3. Save, hide, or move a vacancy into Applications.
4. Review the safety sheet before a real HH action.

Each coach mark:

- is anchored to a visible, currently relevant control;
- never covers the target or essential content;
- shows progress such as `1 of 4`;
- can be dismissed permanently;
- closes on Escape and returns focus to the anchor;
- is skipped when its anchor is unavailable;
- does not block unrelated UI interaction.

Short explanations for terms such as match score use on-demand help popovers, not coach marks. Popovers link to deeper help only when useful.

## Feedback and Overlay System

### Toast

Use for the result of a background or user-triggered action that does not require a decision.

- Success toasts disappear after a reasonable reading interval and pause on hover/focus.
- Warning and error toasts with a recovery action remain until dismissed or resolved.
- Toasts expose an action such as Open, Undo, Configure, or Retry only when that action is valid.
- The region is announced through an appropriate `aria-live` setting without moving focus.

### Popover

Use for short contextual help or compact non-destructive controls. It closes on outside click or Escape, traps no focus, and returns focus to its trigger when closed by keyboard.

### Sheet

Use for onboarding steps, multi-field configuration, and decisions that must be completed or cancelled. Sheets use a visible title, concise consequence text, predictable Cancel placement, focus containment, and focus restoration.

### Live-action safety sheet

Every real HH mutation opens a dedicated confirmation sheet before the request is sent. It shows:

- vacancy and company;
- selected resume;
- cover-letter presence and length;
- the exact live consequence;
- risk flags or blockers;
- an explicit acknowledgement control.

The destructive button remains disabled until the acknowledgement is selected. The client sends the JSON boolean `true`, never a truthy string. The service and transport guards remain authoritative. Cancelling or closing the sheet sends no mutation request.

### Inline error

Use when a specific panel or source failed. The failed region keeps its context, explains what did and did not complete, and provides Retry when safe. Other regions continue to load and function.

### Empty state

Explain why the region is empty and offer one relevant next step. Empty states must not imply that a sync or search succeeded when it did not.

## Component Boundaries

The implementation keeps data operations separate from presentation behavior.

| Unit | Responsibility | Inputs/outputs |
|---|---|---|
| App shell/router | Sidebar, canonical routes, legacy redirects, page title, responsive shell | pathname and navigation events → active destination |
| Today composer | Derive readiness and ordered daily actions from already loaded domain data | profile, sources, resumes, jobs, tasks, events, approvals → view model |
| Onboarding controller | Determine the current step, persist presentation progress, call existing config/resume/sync APIs | readiness plus local progress → sheet state and save actions |
| Guidance controller | Schedule eligible coach marks and permanent dismissals | current route, anchors, progress → at most one visible coach mark |
| Overlay manager | Own a single active sheet/popover, focus handling, Escape behavior, and restoration | overlay descriptor → mounted overlay lifecycle |
| Notification center | Queue, deduplicate, announce, expire, and action toasts | typed notification events → visible toast stack |
| View loaders | Load each route or panel independently and expose loading/error/retry state | API functions → section state |
| Existing domain actions | Sync, score, update status, prepare letters, applications, HH operations | unchanged API contracts and safety guards |

No component owns both server data mutation policy and UI confirmation. Confirmation improves user understanding; server guards enforce safety.

## Data Flow

1. Boot the shell and resolve the current route.
2. Load config/profile, resumes, source status, jobs, calendar/tasks, and agent approvals independently with settled results.
3. Render available regions immediately; skeleton only the regions still loading.
4. Derive the Today view model and onboarding readiness from successful results.
5. If setup is incomplete and not deferred for the session, open the first incomplete onboarding step.
6. Otherwise render Today and schedule the next eligible contextual coach mark after its anchor exists.
7. Mutations flow through existing API helpers. UI components emit typed success, warning, or error events to the notification center.
8. A successful mutation refreshes only affected view models. A failure preserves the user's input and offers a local retry.

## Visual and Responsive Rules

- Use the system font stack beginning with `-apple-system` and `BlinkMacSystemFont`.
- Keep a clear hierarchy between 28–30 px page titles, 15–17 px section titles, 13 px primary content, and 11–12 px metadata/control labels.
- Use an 8 px base spacing rhythm, restrained 8–16 px radii, fine neutral borders, and subtle elevation only for overlays or floating panels.
- Do not use decorative gradients, badges, glass panels, or pills as filler. Translucency is limited to the sidebar/titlebar and must retain contrast without backdrop-filter support.
- Use local SVG icons with one coherent stroke/fill family; emoji and text glyphs are not production icons.
- At narrower desktop widths, collapse the sidebar to icons with accessible labels.
- Below the single-column breakpoint, stack Today regions, keep the primary action visible, and prevent horizontal scrolling.
- Long tables may use a focused mobile row/list representation, but data and actions remain equivalent.

## Accessibility

- All navigation, rows, overlays, coach marks, and toast actions are keyboard reachable.
- Dialog sheets use native or equivalent dialog semantics, initial focus, focus containment, Escape handling where safe, and trigger focus restoration.
- Popovers and tooltips expose accessible relationships to their triggers.
- Icon-only controls have visible tooltips and accessible names.
- Color is never the only status signal.
- Minimum touch/click targets follow a practical 36–44 px control size depending on density.
- Animation is removed or reduced under `prefers-reduced-motion`.

## Error and Edge Cases

- Partial boot failure never aborts unrelated route initialization.
- Stale or deleted records keep unsaved form input and report the conflict locally.
- A coach-mark anchor removed during navigation closes the mark safely without advancing completion.
- A source that becomes unavailable during onboarding remains incomplete and offers Retry or Skip.
- Closing a live-action sheet never counts as confirmation.
- Duplicate notifications merge instead of creating an unbounded stack.
- Local onboarding progress with an unknown future version is ignored safely; product configuration is never reset.
- Existing configured users are not forced to re-enter profile or secret values.
- Masked secrets remain display sentinels and are never persisted as actual credentials.

## Testing and Acceptance

### Contract tests

- New canonical route and legacy redirect mapping.
- Presence of the eight navigation destinations and Today regions.
- Local-only assets, system font stack, responsive rules, dialog semantics, toast live region, and reduced-motion support.
- No production `window.alert` or `window.confirm` remains for application feedback or live-action authorization.

### Browser tests

- Incomplete first launch opens the correct step and resumes it after reload.
- Ready existing configuration skips the wizard.
- Set up later defers only the current session and leaves a Today readiness action.
- Run introduction again resets only presentation progress.
- Coach marks appear in order, at most one new mark per session, and stay dismissed.
- Keyboard focus, Escape, outside click, and focus restoration work for each overlay type.
- Success toast expires; actionable error remains and Retry runs only the failed operation.
- One failed boot request does not prevent other Today regions from rendering.
- Live HH action sends no request on Cancel and sends literal `confirm: true` only after acknowledgement.
- Today, Vacancies, Applications, and Settings retain core workflows after navigation reorganization.
- Desktop, compact-sidebar, and single-column viewport checks have no clipped primary content or horizontal overflow.

### Safety regression

All existing web security, HH confirmation, secret masking, auth preflight, resume editing, job status, and partial-load tests remain passing. The UI is not accepted if visual confirmation becomes the only mutation guard.

## Out of Scope

- Replacing the Python server or service/storage architecture.
- New job-source integrations.
- Autonomous live application behavior.
- Cloud accounts, multi-user synchronization, telemetry, or remote analytics.
- A native macOS binary.
- Rewriting existing domain APIs solely to support presentation.

## Acceptance Criteria

The redesign is ready when:

1. a new user can configure a search goal, choose sources, select/create a resume, and launch the first search through the three-step wizard;
2. a configured user lands on Today and can identify the next useful action without opening another section;
3. every existing major capability remains reachable through the new information architecture;
4. feedback and help use the defined component hierarchy consistently;
5. real HH actions remain explicitly described, auditable, and server-guarded;
6. keyboard, reduced-motion, partial-error, narrow-viewport, and legacy-route tests pass;
7. no material visual or interaction mismatch remains against the approved Apple HIG command-center, onboarding, and state designs.
