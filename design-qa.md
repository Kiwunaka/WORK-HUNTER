# Work Hunter design QA

## Comparison target

- Source visual truth: `C:\Users\kiwun\Документы\work hiring\design\mockups\01-today.png` through `16-settings-advanced.png`.
- Browser implementation captures: `C:\Users\kiwun\Документы\work hiring\design\qa-captures\01-today.png` through `16-settings-advanced.png`.
- Same-input comparison boards:
  - `C:\Users\kiwun\Документы\work hiring\design\qa-comparisons\01-today-comparison.png`
  - `C:\Users\kiwun\Документы\work hiring\design\qa-comparisons\02-vacancies-comparison.png`
  - `C:\Users\kiwun\Документы\work hiring\design\qa-comparisons\11-settings-platforms-comparison.png`
  - `C:\Users\kiwun\Документы\work hiring\design\qa-comparisons\14-settings-appearance-comparison.png`
  - `C:\Users\kiwun\Документы\work hiring\design\qa-comparisons\07-sources-icons-top-comparison.png`
  - `C:\Users\kiwun\Документы\work hiring\design\qa-comparisons\07-sources-icons-list-comparison.png`
- Local implementation: `http://127.0.0.1:8787`.
- State: local data, light theme unless named otherwise, HH unauthenticated, no active resume, technical disclosures closed.

## Viewport and normalization

- Source images: 1536 × 1024 px.
- Browser captures: 1536 × 1024 px at a 1536 × 1024 CSS viewport and `deviceScaleFactor: 1`.
- Comparison boards: two unscaled 1536 × 1024 images placed side by side; no crop or density conversion.
- Additional evidence: `01-today-mobile-680.png` at a 680 × 844 CSS viewport and `14-settings-appearance-dark.png` at 1536 × 1024.
- Sources icon/login evidence was captured in the user-selected in-app browser at 1280 × 720. For the two comparison boards, the 1536 × 1024 source mock was cropped to the matching top/list state and resized to 1280 × 720 before placing it beside the unscaled implementation capture.

## Full-view and focused comparison evidence

All 16 source screens and all 16 rendered routes were captured at the same desktop viewport. The four same-input boards cover the global shell, daily dashboard, dense vacancy workspace, account/login flow and appearance controls. They preserve the generated direction: a permanent left navigation, one dominant action per screen, neutral light surfaces, restrained blue accent, readable Russian labels, compact line icons and technical controls hidden by default.

The visible data differs from the generated examples because the implementation renders the real local store and the actual unauthenticated/readiness state. This is intentional product truth, not design drift. Focused boards were used for the densest regions: vacancy filters and detail actions, HH login/mode/rules, and theme/density controls. Remaining screens use the same tokens and component primitives, so separate crops were not needed after the full-resolution inspection.

## Required fidelity surfaces

- Fonts and typography: Segoe UI Variable/Aptos/Segoe UI system stack; heading weight, line height and 12–16 px interface text remain legible. Long vacancy titles wrap without colliding with scores or source labels. No clipped primary copy was found.
- Spacing and layout rhythm: 12–24 px section rhythm, 10–16 px radii, one-pixel neutral borders and subtle elevation match the generated system. Desktop, 900 px compact and 680 px mobile modes have no document-level horizontal overflow.
- Colors and visual tokens: light palette maps to `#f7f8fa`, white panels and `#0868e8`; green, amber, orange and purple remain semantic accents. The dark palette now wins the cascade and renders body background `rgb(21, 24, 29)` with dark cards and readable foregrounds.
- Image quality and asset fidelity: the design is interface-led and requires no hero imagery. Phosphor icons are vendored locally as a WOFF2 webfont. Source identities use locally vendored site favicons or official raster logos; no runtime CDN, emoji, inline SVG substitute or rasterized UI is used.
- Copy and content: primary labels are human-facing Russian. Login, account, resume, search/apply mode, rules and launch state are above the fold. Raw grants, leases, kill/shadow/canary commands, JSON and API Lab are behind closed advanced disclosures.
- Icons: one consistent Phosphor family is used for navigation, KPIs, controls, states and settings groups; checked classes resolve in the vendored stylesheet.
- States and interactions: selected vacancy, empty resume list, unauthenticated HH, settings sections, search score preview, theme choice, pipeline tabs and closed advanced panels were rendered and exercised.
- Accessibility and resilience: semantic buttons, labels, tabs/radio state and disclosures remain intact; visible focus styling and reduced-motion rules are preserved. Mobile controls stack into full-width tap targets.

## Comparison history

1. Initial audit — P1: the old HH page led with tokens, raw agent flags, kill/recovery/shadow/canary controls and a 16-field grid while the account login was hard to find. Fix: built the human account/login, mode, rules and launch surface; moved service controls under `Техническая панель`. Post-fix evidence: `11-settings-platforms-comparison.png`.
2. First full-screen pass — P2: the vacancy route could open with a blank detail column. Fix: select the first rendered vacancy when no valid deep-link selection exists. Post-fix evidence: `02-vacancies-comparison.png` and the live `/jobs?filter=all` capture with populated `#job-detail`.
3. Assistant pass — P2: at the 1280 × 720 in-app viewport the composer could fall below the visible frame. Fix: constrained the history/chat tracks and removed the minimum-height expansion. Post-fix evidence: in-app browser capture with the composer and send button visible together.
4. Theme pass — P2: choosing dark mode changed state, but the redesign's later light variables won the CSS cascade. Fix: moved the complete dark token override after the redesign palette and replaced hard-coded white surfaces with tokens. Post-fix evidence: `14-settings-appearance-dark.png`, computed body background `rgb(21, 24, 29)`.
5. Responsive/contract pass — P2: the redesigned 210 px sidebar violated the existing full-shell width contract, and the improved calendar label changed its legacy accessible name. Fix: set the full sidebar to 224 px and retained `aria-label="+ Событие"` while keeping the visible `Добавить событие` copy. Post-fix evidence: four responsive/browser tests passed.
6. Final pass: all earlier P1/P2 findings are absent. The 16-route headless capture reported zero console/page errors; the in-app browser development log was empty.
7. Source identity/auth pass — P1: every platform used the same generic globe and `Открыть`, so users could not tell which sources needed a persistent login. Fix: added real platform brand assets across Sources, Vacancies and Applications; added `Нужен вход` states and explicit `Войти в …` actions for HH, LinkedIn, Getmatch, Indeed and RVC; connected browser-platform login buttons to their saved Chromium profiles; kept public boards as `Открыть отклик`; corrected RVC to `app.rvc.global` and Jabka to `jabka.work`. Post-fix evidence: `07-sources-icons-top-comparison.png`, `07-sources-icons-list-comparison.png`, `07-sources-icons-top-iab.png`, `07-sources-icons-list-iab.png` and `02-vacancies-icons-iab.png`. The in-app browser development log remained empty.

## Findings

No actionable P0, P1 or P2 visual or usability findings remain.

## Open questions

- Generated mockups contain illustrative employers, counts and authenticated accounts. The implementation deliberately shows actual local data and never fakes a connected account.

## Primary interactions tested

- All nine settings sections update URL, active state and visible panel.
- Search minimum score updates the live preview from 60 to 73.
- Theme selector applies both light/system and complete dark tokens.
- HH login button is visible as `Войти в HH`; no external login was performed.
- LinkedIn, Getmatch, Indeed and RVC expose explicit login actions backed by the same persistent browser profiles used for application flows; no real account login was performed.
- Public browser-apply sources expose `Открыть отклик`, while Telegram exposes `Открыть канал`.
- Vacancy list selects and renders a real detail panel.
- Applications, analytics and settings subtabs use canonical routes.
- Desktop, compact and 680 px mobile shells have no horizontal document overflow.
- In-app browser development logs: empty.

## Verification

- `tests/test_web_ui_contract.py`: 28 passed.
- Selected source/login/browser security and responsive coverage: 7 passed, 86 deselected.
- Selected browser/UI/navigation/responsive coverage: 14 passed.
- Focused regressions for calendar control and three responsive modes: 4 passed.
- JavaScript syntax checks and `git diff --check`: passed.
- The full browser file was started once but exceeded the 180-second command window; the focused design-relevant subset completed cleanly.

## Follow-up polish

- P3: authenticated account avatars and employer logos will only appear after the corresponding real sources provide them; the unauthenticated state intentionally uses the local icon system.

final result: passed
