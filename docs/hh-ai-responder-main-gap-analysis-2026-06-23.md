# HH AI Responder Gap Analysis - 2026-06-23

## Executive summary

`hh-ai-responder-main` is not a broader product than Work Hunter. It is a much smaller Go daemon that is more aggressive on direct HH automation.

Its main advantage is operational: it uses authenticated HH web cookies and internal web endpoints to run an end-to-end loop:

- raise the active resume every 4 hours;
- search and apply every 24 hours;
- auto-reply in HH chat every 15 minutes;
- answer HH vacancy tests by extracting `vacancyTests` from the response page;
- leave chats when HH marks the applicant state as `DISCARD`;
- write JSONL events for applications, chat replies, and errors.

Work Hunter is stronger as a maintainable local command center: structured storage, tests, MCP/web UI, multiple sources, certification gates, approvals, replay, redaction, account profiles, and safer real-action controls. The gap is not "we are behind overall"; the gap is direct HH web-action coverage and hands-free daemon ergonomics.

## What the new repo does better

1. Direct HH web-session integration.

   It works with exported `cookies.txt`, XSRF, and browser-like headers rather than only official OAuth API paths. This lets it hit endpoints that are difficult or unavailable in the public API.

2. True autonomous loop.

   `Run()` starts three background loops: resume touch every 4 hours, applications every 24 hours, chat replies every 15 minutes.

3. HH chatik API coverage.

   It calls `https://chatik.hh.ru/chatik/api/chats`, `chat_data`, `send`, and `leave`. It also reads message buttons and can answer with one of the offered button texts.

4. Vacancy test extraction from the real response page.

   For vacancies with `userTestPresent`, it opens `/applicant/vacancy_response?...`, extracts embedded `vacancyTests`, asks AI for a strict JSON answer map, and submits the response through `/applicant/vacancy_response/popup`.

5. Practical HH filters.

   It skips archived/labeled/already-response-url vacancies, can skip vacancies above `-mr` max responses, and can run without explicit search URL by using the latest resume.

6. Lightweight portability.

   One Go file, no third-party dependencies, Dockerfile, docker-compose, `start.sh`, and `start.ps1`.

## Where Work Hunter is ahead

1. Safety and auditability.

   Work Hunter requires explicit confirmation for real apply, test answers, chat replies, cleanup, and campaign execution. The Go daemon sends real actions automatically.

2. Test coverage.

   Work Hunter has broad pytest coverage around apply plans, campaign guardrails, question assistant, chat replies, cleanup, source certification, MCP safety, external apply, storage, UI, and transport behavior. The Go repo has no tests in the local copy.

3. Architecture.

   Work Hunter has separate modules for transport, source adapters, campaign planning, approvals, storage, replay, web UI, MCP, resumes, and security. The Go repo is a single `main.go` around 66 KB.

4. Multi-source strategy.

   Work Hunter covers HH plus Habr, GeekJob, Getmatch, Relocate.me, Telegram, and public boards. The Go repo is HH-only.

5. Candidate truth controls.

   Work Hunter has candidate facts, evidence, resume variants, ATS/gap analysis, and confirmation flows. The Go repo prompts the AI to claim any required skills and agree to all conditions, which is risky for reputation.

6. Secret handling and local state.

   Work Hunter has redaction, secret scans, local SQLite state, replay/event storage, and masked API surfaces. The Go repo writes event output but has no comparable state model.

## Main gaps for us

Priority gaps to close:

1. Add an HH web-session adapter backed by cookies/XSRF for endpoints not exposed by the public API.

2. Add `chatik.hh.ru` transport:

   - list chats;
   - fetch chat data;
   - detect last employer message;
   - detect text buttons;
   - send a selected reply;
   - leave/cleanup discarded chats.

3. Add a real HH vacancy-test extractor:

   - open `/applicant/vacancy_response`;
   - extract embedded `vacancyTests`;
   - map tasks and candidateSolutions;
   - produce a planned answer payload;
   - submit only through our existing confirmation/approval gate.

4. Add true resume touch:

   - support `/applicant/resumes/touch` with cookie/XSRF;
   - keep official API `publish` path as fallback;
   - schedule through Work Hunter's safe task runner.

5. Add a "daemon mode" or scheduler profile:

   - resume touch every N hours;
   - campaign planning/apply only when policy enabled;
   - chat scan every N minutes;
   - never bypass kill switch or confirmations.

Secondary gaps:

- import HH search URL query params as a campaign preset;
- max-responses filter for HH campaign planning;
- JSONL event export compatible with the Go tool's `-o results.json`;
- simpler Docker/start scripts for always-on local use.

## What not to copy directly

- Do not copy the autonomous real-send behavior as-is.
- Do not copy prompts that tell the AI to claim every skill or agree to all conditions.
- Do not copy the single-file architecture.
- Do not rely on undocumented HH web endpoints without tests and fallback handling.
- Do not copy substantial donor code without checking license/attribution. No license file was present in the local copy.

## Bugs and fragility found in the new repo

- Local machine has no `go` executable, so I could not compile it here.
- No tests are present.
- README documents `HH_EXTRA_TEST_ANSWER_PROMPT`, but code reads `HH_EXTRA_ЕУЫЕ_ANSWER_PROMPT`.
- README documents `HH_EXTRA_CHAT_REPLY_PROMPT`, but code reads `HH_CHAT_REPLY_PROMPT`.
- `start.sh` and `start.ps1` documentation says they pass arguments through, but the scripts execute the binary without forwarding args.
- It depends on HH internal web endpoints and embedded HTML/JSON markers, so breakage risk is higher than official API use.
- The chat prompt is reputationally dangerous: it asks the model to claim all skills and agree to all conditions.

## Recommended port plan

1. Port behavior, not code: design `HHWebSessionClient` in Python using our existing `hh_transport` style.

2. First slice: resume touch.

   Smallest high-value feature. Implement cookie/XSRF load, call `/applicant/resumes/touch`, add dry-run/confirm/scheduler coverage.

3. Second slice: chatik read-only scan.

   Add list/fetch chat methods, persist observed chat items, expose in UI/API, no send yet.

4. Third slice: chat reply planner with button support.

   Reuse our `HHChatAgentService` approval queue. If buttons exist, present exact options; send only after approval/confirm.

5. Fourth slice: vacancy test extractor.

   Extract `vacancyTests` from the web response page and feed our existing `plan_hh_test_answers` flow. Submit only with explicit confirmation.

6. Fifth slice: daemon profile.

   Add a Work Hunter scheduler preset that runs touch, chat scan, campaign review, and digest without bypassing policy gates.

## Bottom line

We are not behind in platform quality. We are behind in direct HH web automation coverage and always-on ergonomics.

The best things to take are:

- HH cookie/XSRF web-session transport;
- `chatik.hh.ru` chat endpoints;
- embedded `vacancyTests` extraction and payload mapping;
- `/applicant/resumes/touch`;
- simple daemon cadence and result JSONL export.

Everything should be wrapped in Work Hunter's existing approval, replay, redaction, and kill-switch model.
