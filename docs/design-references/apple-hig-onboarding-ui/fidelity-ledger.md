# Apple HIG UI fidelity ledger

## Reference-to-implementation mapping

| Reference intent | Implemented surface | Result |
|---|---|---|
| Eight-item translucent command sidebar | Full 232 px sidebar and 72 px compact sidebar | Matched; local outline SVG icons replace concept-only symbols |
| Today readiness and primary search action | `#today-readiness` with `today.find-vacancies` | Matched |
| Bounded focus, fresh-match, upcoming, and decision regions | `#today-focus`, `#today-fresh-matches`, `#today-upcoming`, `#today-decisions` | Matched; genuine empty states replace illustrative fake records |
| Centered first-run sheet with dimmed app context | Three-step onboarding through the shared overlay manager | Matched |
| Quiet Apple-like typography, separators, blue/green/amber/red semantics | System font stack and local CSS tokens | Matched |
| Safety confirmation, popover help, and error/empty variants | Typed live-action sheet, notification center, inline resource states | Matched |
| Narrow layout | Mobile app bar, blocking menu sheet, one-column Today | Added from the responsive specification |

## Intentional differences

- The visual references contain fictional vacancies and events. Production renders only loaded records, so the clean QA profile shows explicit empty states.
- The concept greeting is time-specific. Production uses the stable destination title **Сегодня** to avoid hidden clock-dependent copy.
- Company logos and decorative illustration are omitted because runtime assets must remain local and the product does not store verified logo media.

## QA evidence

- `implementation-desktop.png` — 1440×900, zero horizontal overflow.
- `implementation-compact.png` — 900×900, 72 px sidebar, zero horizontal overflow.
- `implementation-mobile.png` — 680×844, single-column layout, zero horizontal overflow.
- `implementation-mobile-menu.png` — all eight destinations in the mobile sheet.
- `implementation-onboarding.png` — settled onboarding sheet after motion completion.
- Browser console/page error count was zero at all three acceptance viewports.
