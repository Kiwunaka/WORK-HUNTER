import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const outputDir = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.resolve(outputDir, "..", "..");
const today = "2026-06-24";
const currentPytestEvidence = "Current-cycle automated retest: python -m pytest -q -> 481 passed in 129.14s on 2026-06-24. Pytest emitted an ignored Windows temp cleanup PermissionError after completion.";
const workbookPath = path.join(outputDir, "work_hunter_feature_ledger.xlsx");
const overridesPath = path.join(outputDir, "qa-overrides.json");

const files = {
  html: path.join(projectRoot, "work_hunter", "web", "static", "index.html"),
  js: path.join(projectRoot, "work_hunter", "web", "static", "app.js"),
  css: path.join(projectRoot, "work_hunter", "web", "static", "app.css"),
  server: path.join(projectRoot, "work_hunter", "web", "server.py"),
  cli: path.join(projectRoot, "work_hunter", "cli.py"),
  services: path.join(projectRoot, "work_hunter", "services.py"),
};

const readText = async (file) => fs.readFile(file, "utf8");

const html = await readText(files.html);
const js = await readText(files.js);
const css = await readText(files.css);
const server = await readText(files.server);
const cli = await readText(files.cli);
const services = await readText(files.services);

const qaOverrides = await loadJson(overridesPath);

const featureRows = [
  story("WH-US-001", "Setup", "Web", "As the owner, I can open Setup and see local readiness, AI readiness, and doctor-style diagnostics before using the app.", "Setup refresh calls init/status, doctor, and AI status routes, renders readiness badges, and surfaces global UI errors without leaking secrets.", "work_hunter/web/static/index.html:view-setup; work_hunter/web/static/app.js:loadSetupStatus,renderAiReadiness", "/api/init/status, /api/doctor, /api/ai/status", "tests/test_static_ui_roadmap.py::test_static_ui_exposes_ai_readiness_and_error_ux; tests/test_api_design_aliases.py::test_generic_setup_ai_source_browser_and_resume_utility_aliases", "Medium"),
  story("WH-US-002", "Setup", "Web/API", "As the owner, I can run an AI dry run from Setup to verify the configured route safely.", "The AI test control sends route and prompt, supports dry_run behavior, records runs, and reports failures in the output/error line.", "work_hunter/web/static/app.js:testAiRoute; work_hunter/services.py:ai_test", "/api/ai/test, /api/ai/run, /api/ai/runs", "tests/test_work_hunter_command_center.py::test_ai_status_and_test_never_read_external_auth_cache", "Medium"),
  story("WH-US-003", "Setup", "Web/API", "As the owner, I can preview and apply WO/FLOW imports with redaction-aware output.", "Preview reports what would import; apply writes only after explicit action and must not expose secrets in rendered output.", "work_hunter/web/static/app.js:previewSetupWoImport,applySetupWoImport", "/api/init/import-wo/preview, /api/init/import-wo", "tests/test_static_ui_roadmap.py::test_static_ui_exposes_setup_wo_import_controls", "High"),
  story("WH-US-004", "Jobs", "Web", "As a job seeker, I can sync sources and rescore jobs from the inbox toolbar.", "Sync posts score=true, updates the summary line, reloads jobs and source readiness, and disables the button while pending.", "work_hunter/web/static/app.js:syncJobs,scoreJobs", "/api/sync, /api/score", "tests/test_services.py::test_sync_sources_default_includes_public_board_sources; tests/test_services.py::test_score_existing_jobs", "Medium"),
  story("WH-US-005", "Jobs", "Web/API", "As a job seeker, I can view the job queue filtered by source and minimum score.", "Inbox loads up to 200 jobs, sends source/min_score query parameters, renders score/source/status columns, and updates the summary count.", "work_hunter/web/static/app.js:loadJobs,renderJobs", "/api/inbox, /api/jobs", "tests/test_web_roadmap_surface.py::test_web_api_inbox_alias_lists_filtered_job_queue", "High"),
  story("WH-US-006", "Jobs", "Web", "As a job seeker, I can apply smart filters for remote-only, salary, and seniority without refetching.", "Smart filters transform the current in-memory job list and preserve table layout, selection affordances, and count summary.", "work_hunter/web/static/app.js:applySmartFilters,renderFilteredJobs", "client-side filter over /api/inbox data", "tests/test_static_ui_roadmap.py::test_static_ui_exposes_roadmap_views_and_controls", "Medium"),
  story("WH-US-007", "Jobs", "Web/API", "As a job seeker, I can run AI search across job descriptions.", "AI search sends a free-text query, renders matching jobs, and reports API errors through the shared error line.", "work_hunter/web/static/app.js:aiSearch", "/api/jobs/search-ai", "tests/test_static_ui_roadmap.py::test_static_ui_has_roadmap_js_functions_and_event_hooks", "Medium"),
  story("WH-US-008", "Jobs", "Web/API", "As a job seeker, I can export the current jobs list to CSV.", "The export control builds a jobs/export URL with active filters and downloads CSV without mutating job state.", "work_hunter/web/static/app.js:exportCsv; work_hunter/services.py:export_jobs", "/api/jobs/export", "tests/test_static_ui_roadmap.py::test_static_ui_has_roadmap_js_functions_and_event_hooks", "Low"),
  story("WH-US-009", "Jobs", "Web/API", "As a job seeker, I can select a job and inspect its score, reasons, red flags, description, and latest letter.", "Selecting a row highlights it, fetches detail, fills job-id controls across views, shows AI panels, and loads notes.", "work_hunter/web/static/app.js:selectJob,renderJobDetailHtml,fillSelectedJobControls", "/api/jobs/{id}, /api/jobs/{id}/note", "tests/test_static_ui_roadmap.py::test_static_ui_exposes_dedicated_job_detail_screen", "High"),
  story("WH-US-010", "Jobs", "Web/API", "As a job seeker, I can mark one job saved, hidden, or applied.", "Status changes persist, applied also records an application when supported, a status event is recorded, and the queue/detail refresh.", "work_hunter/web/static/app.js:markSelected; work_hunter/services.py:mark_job", "/api/jobs/{id}/status, /api/jobs/{id}/apply, /api/jobs/{id}/record-event", "tests/test_storage.py::test_upsert_job_and_status; tests/test_services.py::test_prepare_letter_for_existing_job", "High"),
  story("WH-US-011", "Jobs", "Web/API", "As a job seeker, I can bulk select jobs and apply saved/hidden/applied actions.", "Select-all and per-row checkboxes update the bulk toolbar; bulk actions persist status for selected IDs and clear selection afterwards.", "work_hunter/web/static/app.js:toggleSelectAll,toggleJobSelect,bulkAction", "/api/jobs/bulk", "tests/test_static_ui_roadmap.py::test_static_ui_exposes_roadmap_views_and_controls", "High"),
  story("WH-US-012", "Jobs", "Web/API", "As a job seeker, I can open a dedicated job detail screen by ID.", "The Job Detail view accepts a job ID or selected ID, fetches the job, and renders the same detail format without inbox action buttons.", "work_hunter/web/static/app.js:loadJobDetailView", "/api/jobs/{id}", "tests/test_static_ui_roadmap.py::test_static_ui_exposes_dedicated_job_detail_screen", "Medium"),
  story("WH-US-013", "Jobs", "Web/API", "As a job seeker, I can save and reload notes per selected job.", "Notes load when a job is selected and save to storage without changing the job's status.", "work_hunter/web/static/app.js:saveJobNote,loadJobNote", "/api/jobs/{id}/note", "tests/test_storage.py::test_save_score_and_letter", "Medium"),
  story("WH-US-014", "Jobs", "External", "As a job seeker, I can share a selected job to Telegram.", "The Telegram action opens a share URL using the selected job title, company, salary, and URL without sending through backend state.", "work_hunter/web/static/app.js:shareToTelegram", "https://t.me/share/url", "manual/browser QA", "Low"),
  story("WH-US-015", "AI Assistance", "Web/API", "As a job seeker, I can generate rule-based and AI cover letters for a selected job.", "Template and AI letter actions write draft text into the letter editor and surface AI failures with useful configuration guidance.", "work_hunter/web/static/app.js:prepareLetter,prepareLetterAi; work_hunter/services.py:prepare_letter,prepare_letter_ai", "/api/jobs/{id}/letter, /api/jobs/{id}/letter-ai", "tests/test_scoring_letters.py::test_draft_cover_letter_contains_company_and_title; tests/test_ai_backend.py::test_ai_cover_letter_uses_opencode_without_direct_api_key", "Medium"),
  story("WH-US-016", "AI Assistance", "Web/API", "As a job seeker, I can preview human cover letter templates and choose one for campaign use.", "The preview action returns variants, displays JSON, and optionally writes the selected campaign letter back into the editor.", "work_hunter/web/static/app.js:previewHumanLetter; work_hunter/services.py:cover_letter_preview", "/api/jobs/{id}/letter-preview", "tests/test_static_ui_roadmap.py::test_static_ui_exposes_human_cover_letter_preview_controls; tests/test_human_cover_letters.py", "Medium"),
  story("WH-US-017", "AI Assistance", "Web/API", "As a job seeker, I can request resume tips, an ATS resume, and ATS audit for a selected job.", "Each action requires a selected job or resume text, posts to its endpoint, and renders markdown or audit output in the AI panel.", "work_hunter/web/static/app.js:getResumeTips,getAtsResume,getAtsAudit; work_hunter/services.py:resume_tips,ats_resume,ats_audit", "/api/jobs/{id}/resume-tips, /api/jobs/{id}/ats-resume, /api/ats-audit", "tests/test_resume_engine.py; tests/test_static_ui_roadmap.py", "Medium"),
  story("WH-US-018", "AI Assistance", "Web/API", "As a job seeker, I can get AI fit, summary, interview questions, and experience pitch for a selected job.", "The selected job drives AI analysis panels, fit score badge, markdown output, and loading states for each analysis action.", "work_hunter/web/static/app.js:getAiFit,getSummary,getInterviewQuestions,getExperiencePitch", "/api/jobs/{id}/ai-fit, /api/jobs/{id}/summarize, /api/jobs/{id}/interview-questions, /api/jobs/{id}/pitch", "tests/test_hh_ai_filter_stage.py; tests/test_pipeline_interview.py", "Medium"),
  story("WH-US-019", "AI Assistance", "Web/API", "As a job seeker, I can fetch full descriptions, parse job structure, run gap analysis, and smart-classify jobs.", "Each job action posts to its route, updates the relevant output area or alert, and does not submit applications.", "work_hunter/web/static/app.js:fetchFullDescription,parseJobStructure,runGapAnalysis,smartClassify", "/api/jobs/{id}/fetch-full, /api/jobs/{id}/parse-structure, /api/jobs/{id}/gap-analysis, /api/jobs/{id}/smart-classify", "tests/test_hh_ai_filter_stage.py; tests/test_resume_engine.py", "Medium"),
  story("WH-US-020", "AI Assistance", "Web/API", "As a job seeker, I can chat with the AI assistant with optional current-job context.", "Chat messages render in user/assistant bubbles, include selected job when attached, and preserve chat history in UI state.", "work_hunter/web/static/app.js:sendChatMessage,renderChat,updateChatContext", "/api/chat", "tests/test_hh_agent_chat_service.py", "Medium"),
  story("WH-US-021", "Candidate", "Web/API", "As the owner, I can answer onboarding questions and turn answers into candidate facts.", "Questions load from the API; answers save facts with source/status and update onboarding completeness.", "work_hunter/web/static/app.js:loadOnboardingQuestions,submitOnboardingAnswer; work_hunter/services.py:answer_onboarding", "/api/onboarding/questions, /api/onboarding/answer", "tests/test_hh_onboarding.py; tests/test_work_hunter_command_center.py::test_candidate_fact_confirmation_controls_resume_variant_claims", "Medium"),
  story("WH-US-022", "Candidate", "Web/API", "As the owner, I can inspect candidate map facts and confirm or reject them.", "Candidate map renders facts/completeness; confirm/reject routes update fact status and replay/audit context.", "work_hunter/web/static/app.js:loadCandidateMap,confirmCandidateFact", "/api/candidate/map, /api/candidate/facts, /api/candidate/confirm-fact, /api/onboarding/confirm-fact, /api/onboarding/reject-fact", "tests/test_candidate_map.py; tests/test_api_design_aliases.py::test_generic_candidate_and_resume_api_aliases", "Medium"),
  story("WH-US-023", "Candidate", "Web/API", "As the owner, I can edit and switch the active search profile.", "Profile settings support desired roles, queries, stop words, must-have skills, nice skills, active profile switching, and rescore prompt.", "work_hunter/web/static/app.js:loadProfile,renderProfileForm,switchProfile,saveProfile", "/api/profile, /api/profile/switch, /api/candidate/profile", "tests/test_config.py; tests/test_api_design_aliases.py::test_generic_candidate_and_resume_api_aliases", "Medium"),
  story("WH-US-024", "Resumes", "Web/API", "As a job seeker, I can create, edit, activate, delete, and import resumes.", "Resume list loads from storage; form save persists text; activate/delete mutate selected resume; import reads a local path and can activate the imported resume.", "work_hunter/web/static/app.js:loadResumes,saveResume,activateResume,deleteResume,importResume", "/api/resumes, /api/resumes/import, /api/resumes/{id}/activate, /api/resumes/{id}/delete", "tests/test_static_ui_roadmap.py::test_static_ui_exposes_resume_import_and_variant_diff_controls; tests/test_resume_engine.py", "High"),
  story("WH-US-025", "Resumes", "Web/API", "As a job seeker, I can build a resume variant for a target job using confirmed facts.", "Build validates job/resume IDs, creates a variant, shows JSON output, and renders a diff from the policy result.", "work_hunter/web/static/app.js:buildResumeVariant,renderResumeVariantDiff; work_hunter/services.py:build_resume_variant", "/api/resume-variants/build, /api/resumes/build-variant, /api/resumes/variants, /api/resumes/variants/{id}/diff", "tests/test_web_roadmap_surface.py::test_web_api_builds_resume_variant_after_confirmed_fact", "High"),
  story("WH-US-026", "Applications", "Web/API", "As a job seeker, I can build an application pack preview from a job, resume variant, cover letter, and source payload.", "Preview masks secrets, computes policy status, and stores an application pack that can be inspected later.", "work_hunter/web/static/app.js:buildApplicationPreview; work_hunter/services.py:build_application_pack", "/api/applications/build-pack, /api/applications/{id}, /api/applications/{id}/preview", "tests/test_web_roadmap_surface.py::test_web_api_application_pack_preview_masks_secrets; tests/test_application_pack_templates.py", "High"),
  story("WH-US-027", "Applications", "Web/API", "As a job seeker, I can dry-run external application forms and keep real submit gated.", "Dry run maps form/persona data and returns submit=false; confirm external apply blocks unless the source is certified and the user explicitly requests certified submit.", "work_hunter/web/static/app.js:dryRunExternalApply,confirmExternalApply; work_hunter/services.py:external_apply_dry_run,confirm_external_apply", "/api/jobs/{id}/external-apply/dry-run, /api/jobs/{id}/external-apply/confirm", "tests/test_external_apply_executor.py; tests/test_api_design_aliases.py::test_generic_application_api_aliases_preview_dry_run_and_apply_block", "High"),
  story("WH-US-028", "HH Apply", "Web/API", "As a job seeker, I can prepare an HH apply plan before any real HH application is sent.", "Apply plan uses selected job and letter, returns status/reasons/resume choice, and does not submit.", "work_hunter/web/static/app.js:applyHh(true); work_hunter/services.py:prepare_apply_plan", "/api/jobs/{id}/apply-plan", "tests/test_apply_plan.py::test_prepare_apply_plan_for_hh_uses_exact_vacancy_without_sending", "High"),
  story("WH-US-029", "HH Apply", "Web/API", "As a job seeker, I can only confirm a real HH apply after an explicit browser confirmation and a ready plan.", "Confirm prompts the user, recomputes plan, blocks unsafe plans, and posts confirm=true only for ready HH plans.", "work_hunter/web/static/app.js:applyHh(false); work_hunter/services.py:confirm_apply", "/api/jobs/{id}/confirm-apply", "tests/test_apply_plan.py::test_confirm_apply_requires_explicit_confirmation; tests/test_apply_plan.py::test_confirm_apply_blocks_hh_hard_risk_flags_before_real_send", "Critical"),
  story("WH-US-030", "Calendar", "Web/API", "As a job seeker, I can create and delete calendar events attached to jobs.", "Calendar view lists events, save persists title/type/date/job/notes, and delete removes the event from storage.", "work_hunter/web/static/app.js:loadEvents,saveEvent,deleteEvent", "/api/events, /api/events/{id}/delete", "tests/test_pipeline_interview.py", "Medium"),
  story("WH-US-031", "Pipeline", "Web/API", "As a job seeker, I can inspect pipeline status for a job.", "Pipeline status fetches stage, follow-up suggestions, next actions, and related events/applications for a job ID.", "work_hunter/web/static/app.js:loadPipelineStatus; work_hunter/services.py:pipeline_status", "/api/pipeline/jobs/{id}", "tests/test_static_ui_roadmap.py::test_static_ui_exposes_pipeline_and_interview_prep_controls; tests/test_pipeline_interview.py", "Medium"),
  story("WH-US-032", "Pipeline", "Web/API", "As a job seeker, I can build prep packs and schedule follow-ups from Pipeline and Interview Prep.", "Prep pack uses selected stage/job; schedule follow-up creates an event and reloads pipeline status.", "work_hunter/web/static/app.js:buildPipelinePrepPack,buildInterviewPrepPack,schedulePipelineFollowup", "/api/pipeline/jobs/{id}/prep-pack, /api/pipeline/jobs/{id}/event", "tests/test_pipeline_interview.py; tests/test_static_ui_roadmap.py::test_static_ui_exposes_dedicated_interview_prep_screen", "Medium"),
  story("WH-US-033", "Settings", "Web/API", "As the owner, I can save JSON config, HH token flags, AI settings, auto-sync, saved searches, and ghost-job checks.", "Settings write config/profile/search state locally, start/stop auto-sync timers, and keep secrets masked in displayed config.", "work_hunter/web/static/app.js:saveConfig,saveHhToken,saveAiSettings,saveAutoSync,saveSearch,loadGhostJobs", "/api/config, /api/saved-searches, /api/ghost-jobs", "tests/test_config.py; tests/test_security_redaction.py", "High"),
  story("WH-US-034", "Sources", "Web/API", "As the owner, I can inspect source readiness and capabilities across job boards.", "Sources view loads source list, readiness badges, and capabilities/certification matrix without requiring a sync first.", "work_hunter/web/static/app.js:loadSources,renderSourceReadiness,renderSourceCertificationMatrix", "/api/sources, /api/source-status, /api/sources/certification-matrix", "tests/test_source_adapter_registry.py::test_source_status_api_exposes_registry_report; tests/test_static_ui_roadmap.py::test_static_ui_exposes_source_readiness_badges", "Medium"),
  story("WH-US-035", "Sources", "Web/API", "As the owner, I can run safe per-source sync and test actions.", "Source sync with limit=0 returns planned/dry-run; source test reports adapter status and errors without real application side effects.", "work_hunter/web/static/app.js:syncSelectedSource,testSelectedSource; work_hunter/services.py:sync_sources,source_capabilities", "/api/sources/{source}/sync, /api/sources/{source}/test", "tests/test_api_design_aliases.py::test_generic_source_and_browser_api_aliases", "Medium"),
  story("WH-US-036", "Sources", "Web/API", "As the owner, I can manage source certification evidence, plans, redaction scans, and promotions.", "Certification plan describes missing evidence; evidence/redaction endpoints persist proof; promotion blocks incomplete evidence and never silently increases maturity.", "work_hunter/web/static/app.js:loadSourceCertificationPlan,recordSourceCertificationEvidence,recordSourceRedactionScan,promoteSourceCertification", "/api/sources/certification-plan, /api/sources/{source}/certification-evidence, /api/sources/{source}/redaction-scan, /api/sources/{source}/certify", "tests/test_source_adapter_registry.py", "High"),
  story("WH-US-037", "Sources", "Web/API", "As the owner, I can configure external apply targets directly or from HAR files.", "Target configuration saves session/method/url/template without promotion; HAR import redacts secrets and can configure apply targets when requested.", "work_hunter/web/static/app.js:configureSourceExternalApplyTarget,configureSourceExternalApplyFromHar", "/api/sources/{source}/external-apply-target, /api/sources/{source}/external-apply-from-har", "tests/test_source_adapter_registry.py::test_source_external_apply_target_api_configures_session_and_url; tests/test_browser_session_lab.py::test_getmatch_har_import_configures_external_apply_target_from_recon", "High"),
  story("WH-US-057", "Sources", "Web/API", "As a newcomer, I can use a global Source Setup Wizard that explains how to connect HH or another source without exposing secrets or sending applications.", "The wizard opens from global entry points, defaults to the simple HH path, classifies the source lane, shows steps/next action/logs, masks diagnostics, hides HAR/dry-run/redaction in advanced controls, allows only whitelisted safe actions, and ends at existing explicit confirm flows rather than submitting applications.", "work_hunter/web/static/index.html:source-setup-modal; work_hunter/web/static/app.js:openSourceSetupWizard,loadSourceSetupGuide,runSourceSetupAction; work_hunter/services.py:source_setup_guide,source_setup_action; work_hunter/web/server.py:/api/source-setup/*", "/api/source-setup/guide, /api/source-setup/action", "tests/test_source_setup_wizard.py; tests/test_static_ui_roadmap.py::test_static_ui_source_setup_defaults_to_simple_hh_path", "High"),
  story("WH-US-038", "Browser Lab", "Web/API", "As the owner, I can manage browser-lab login/session status and HAR import for external sources.", "Browser Lab reports source status, opens planned login profiles, imports HAR with allowed hosts, and redacts recorded session data.", "work_hunter/web/static/app.js:loadBrowserLabStatus,openBrowserLabLogin,importBrowserLabHar", "/api/browser-lab/status, /api/browser-lab/open-login, /api/browser-lab/import-har", "tests/test_browser_session_lab.py; tests/test_static_ui_roadmap.py::test_static_ui_exposes_browser_session_lab_controls", "High"),
  story("WH-US-039", "Browser Lab", "Web/API", "As the owner, I can map, dry-run, and execute dry-run form fills without submitting unknown forms.", "Form mapping uses persona/source payload context, dry-run produces planned fill actions and screenshots, execute-dry-run fills but does not submit.", "work_hunter/web/static/app.js:mapBrowserLabForm,dryRunBrowserLabForm,executeBrowserLabDryRun", "/api/browser-lab/forms/map, /api/browser-lab/forms/dry-run, /api/browser-lab/forms/execute-dry-run", "tests/test_browser_session_lab.py::test_browser_lab_form_mapping_and_dry_run_never_submit_unknown_forms", "Critical"),
  story("WH-US-040", "Security", "Web/API", "As the owner, I can audit security status and run ad-hoc redaction scans.", "Audit view shows source/security status and redaction scan output where bearer tokens, cookies, and client secrets are masked.", "work_hunter/web/static/app.js:loadSecurityStatus,runAuditSecurityRedactionScan; work_hunter/services.py:security_status", "/api/security/status, /api/init/redaction-scan", "tests/test_security_redaction.py; tests/test_static_ui_roadmap.py::test_static_ui_exposes_audit_security_redaction_scan", "High"),
  story("WH-US-041", "Replay", "Web/API", "As the owner, I can inspect and export replay timelines for jobs and campaign runs.", "Replay supports job/run inputs, source/event filters, markdown export, and screenshot retrieval while preserving local path safety.", "work_hunter/web/static/app.js:loadReplayTimeline,replayQueryParams,exportReplayMarkdown; work_hunter/services.py:replay_for_job,replay_for_run,export_replay_markdown", "/api/replay/jobs/{id}, /api/replay/runs/{id}, /api/replay/events/{id}/screenshot", "tests/test_replay_timeline.py; tests/test_api_design_aliases.py::test_generic_replay_event_screenshot_serves_local_file", "Medium"),
  story("WH-US-042", "Campaigns", "Web/API", "As the owner, I can plan, review, enable, confirm, pause, resume, kill, and replay HH campaigns.", "Campaigns respect filters, AI filter mode, daily caps, pause state, kill switch, explicit confirmation, and replay events.", "work_hunter/web/static/app.js:loadCampaignRuns,planHhCampaign,confirmCampaignRun,killCampaigns,resumeCampaigns", "/api/hh/campaigns/plan, /api/hh/campaigns/{id}/confirm, /api/agent/pause, /api/agent/resume", "tests/test_campaign_engine_safety.py; tests/test_api_design_aliases.py::test_generic_campaign_api_aliases_plan_review_pause_and_replay", "Critical"),
  story("WH-US-043", "Campaigns", "Web/API", "As the owner, I can plan and gated-confirm external campaigns for certified sources.", "External campaign plan blocks missing session/certification; run-external requires explicit confirmation and certified L6 evidence.", "work_hunter/web/static/app.js:planExternalCampaign,confirmExternalCampaignRun; work_hunter/services.py:plan_external_campaign,confirm_external_campaign", "/api/campaigns/external/plan, /api/campaigns/{id}/run-external", "tests/test_api_design_aliases.py::test_generic_external_campaign_api_alias_runs_with_explicit_confirmation_gate", "Critical"),
  story("WH-US-044", "HH Agent", "Web/API", "As the owner, I can inspect HH agent preflight, digest, events, tasks, operations, approvals, templates, and blacklist.", "Agent cockpit renders dashboard metrics, inbox panels, approval lists, operation logs, and settings panels from separate API calls.", "work_hunter/web/static/app.js:loadAgentCockpit,renderAgentDashboard,renderAgentInbox,renderAgentSettings", "/api/agent/preflight, /api/agent/digest, /api/agent/events, /api/agent/tasks, /api/operations, /api/approvals, /api/templates, /api/blacklist", "tests/test_hh_agent_events.py; tests/test_hh_agent_approval.py; tests/test_static_ui_roadmap.py::test_static_ui_exposes_campaign_safety_controls", "High"),
  story("WH-US-045", "HH Agent", "Web/API", "As the owner, I can run and cancel named HH agent operations.", "Operation buttons call agent/run with operation params, operation status is trackable, and cancel updates operation status without hidden side effects.", "work_hunter/web/static/app.js:runAgentOperation,cancelAgentOperation; work_hunter/services.py:run_hh_agent_operation,cancel_hh_agent_operation", "/api/agent/run, /api/cancel/{id}, /api/agent/operations/{id}", "tests/test_hh_operations.py; tests/test_hh_agent_policy.py", "High"),
  story("WH-US-046", "HH Agent", "Web/API", "As the owner, I can approve, reject, modify, or flag HH approval messages.", "Approval actions update pending messages/outbox state with reason or replacement body and refresh the approvals panel.", "work_hunter/web/static/app.js:approveAgentApproval,rejectAgentApproval,modifyAgentApproval,flagAgentApproval", "/api/approvals/{id}/approve, /api/approvals/{id}/reject, /api/approvals/{id}/modify, /api/approvals/{id}/flag", "tests/test_hh_agent_approval.py", "Critical"),
  story("WH-US-047", "HH Agent", "Web/API", "As the owner, I can save/delete HH letter templates and employer blacklist entries.", "Settings forms persist templates/blacklist entries and refresh lists after save/delete.", "work_hunter/web/static/app.js:saveAgentTemplate,deleteAgentTemplate,saveAgentBlacklist,deleteAgentBlacklist", "/api/templates, /api/templates/delete, /api/blacklist, /api/blacklist/delete", "tests/test_hh_agent_resume_templates.py; tests/test_hh_agent_policy.py", "Medium"),
  story("WH-US-048", "HH API Lab", "Web/API", "As the owner, I can run HH API lab quick calls, custom calls, and reusable snippets safely.", "Lab request normalizes method/path/params/body, blocks unsafe data exposure, stores snippets, and displays JSON output.", "work_hunter/web/static/app.js:runHhLabCall,runHhLabQuick,saveHhLabSnippet,deleteHhLabSnippet", "/api/hh/lab/call, /api/hh/lab/quick-calls, /api/hh/lab/snippets, /api/hh/lab/snippets/delete", "tests/test_hh_api_lab.py", "High"),
  story("WH-US-049", "Stats", "Web/API", "As a job seeker, I can view aggregate job statistics and funnel distribution.", "Stats view loads total/new/applied/saved counts plus source, score, and funnel breakdowns.", "work_hunter/web/static/app.js:loadStats; work_hunter/storage.py:get_stats", "/api/stats", "tests/test_storage.py; tests/test_static_ui_roadmap.py", "Low"),
  story("WH-US-050", "Market", "Web/API", "As a job seeker, I can generate market trend analysis from recent jobs.", "Trends view posts a limit, renders markdown output, and reports AI errors inline.", "work_hunter/web/static/app.js:loadMarketTrends; work_hunter/services.py:market_trends", "/api/market-trends", "tests/test_static_ui_roadmap.py", "Medium"),
  story("WH-US-051", "CLI", "CLI", "As the owner, I can use the CLI for init, sync, score, list, letters, status, reports, config, UI, and MCP server startup.", "CLI parser exposes local-first commands and routes to WorkHunter methods without printing raw secrets.", "work_hunter/cli.py:add_parser; work_hunter/services.py", "work-hunter init/sync/score/list/letter/status/report/config/ui/mcp", "tests/test_cli_design_surface.py; tests/test_agent_orchestrator.py", "Medium"),
  story("WH-US-052", "CLI", "CLI/API", "As the owner, I can use CLI HH commands for auth, resumes, negotiations, campaigns, presets, API lab, and apply-from-file.", "HH CLI commands preserve dry-run/confirmation gates, support account profiles, and mask sensitive auth material.", "work_hunter/cli.py:hh-* parsers; work_hunter/services.py:hh_* methods", "work-hunter hh-*", "tests/test_hh_auth.py; tests/test_hh_resume_operations.py; tests/test_hh_campaign_outcomes.py; tests/test_hh_agent_apply_from_file.py", "High"),
  story("WH-US-053", "CLI", "CLI/API", "As the owner, I can use CLI source and browser commands for source certification, external apply, and browser session lab work.", "Source/browser CLI commands mirror web/API safety gates for certification, HAR import, redaction, dry-run, and manual handoff.", "work_hunter/cli.py:source,browser parsers", "work-hunter source *, work-hunter browser *", "tests/test_source_adapter_registry.py; tests/test_browser_session_lab.py", "High"),
  story("WH-US-054", "CLI", "CLI/API", "As the owner, I can run API recon, API discovery/probe, external session import/list/show/call, and external adapter planning.", "Recon tools redact secret values, restrict hosts, classify endpoints, and require unsafe flags for real mutating lab calls.", "work_hunter/cli.py:api-recon-har,api-discover-url,api-probe-url,external-session", "work-hunter api-* external-*", "tests/test_api_recon.py; tests/test_api_discovery.py; tests/test_api_probe.py; tests/test_external_sessions.py", "High"),
  story("WH-US-055", "MCP", "MCP/API", "As an agent workflow, I can access Work Hunter through MCP tools while preserving safety policy.", "MCP exposes discovery/action tools, blocks silent real applications, records redacted runs, and surfaces dry-run/manual review states.", "work_hunter/mcp_server.py; work_hunter/hh_agent/mcp_handlers.py", "python -m work_hunter mcp", "tests/test_mcp_surface.py; tests/test_mcp_safety.py; tests/test_hh_agent_mcp.py", "Critical"),
  story("WH-US-056", "Safety", "System", "As the owner, I can trust local storage, audit logs, replay records, and redaction helpers to keep secrets private.", "Storage and security helpers mask tokens/cookies/client secrets in config, logs, replay, AI runs, source evidence, and reports.", "work_hunter/storage.py; work_hunter/security/redaction.py; work_hunter/config.py", "redaction utilities and storage methods", "tests/test_security_redaction.py; tests/test_scheduler.py; tests/test_agent_orchestrator.py", "Critical"),
];

const referenceRows = [
  reference("s3rgeym/hh-applicant-tool", "https://github.com/s3rgeym/hh-applicant-tool", "fb4667a3d31d59a5c98a39c0b208c8b9abc093df", "Simple local HH automation with cover letters, apply-time tests, optional employer email/chat follow-up, local personal-data storage, multi-account/resume support, CLI/headless operation, AI filtering, captcha handling, skipped-job tracking, and UI.", "WH-US-015, WH-US-028, WH-US-029, WH-US-042, WH-US-044, WH-US-048, WH-US-052, WH-US-056, WH-US-057", "Primary GitHub README opened 2026-06-24; key-features section includes cover letters, apply tests, chat, local data safety, multi-account/resume, CLI/headless, AI filtering, captcha, skipped jobs, and UI."),
  reference("0FL01/hh-applicant-tool", "https://github.com/0FL01/hh-applicant-tool", "cfc3d6fcab7f4714340c7ec83c308c0a32e3611a", "Operational command baseline: authorize, profile selection, dry-run apply-vacancies, excluded filters, update-resumes, reply-employers, live logs, config get/set/edit, SQLite query/export, and negotiation cleanup.", "WH-US-023, WH-US-028, WH-US-029, WH-US-042, WH-US-044, WH-US-045, WH-US-052", "Primary GitHub README opened 2026-06-24; command examples show authorize/profile/dry-run/apply/update/reply/log/config/query/cleanup flows."),
  reference("s3rgeym/hh-ai-responder", "https://github.com/s3rgeym/hh-ai-responder/tree/main", "a277e1992e9310f4bdea5d1ded1986974d59ffb1", "Always-on responder baseline: search URL/cookie fallback, OpenAI-compatible env config, AI model/key/prompts/contacts, Docker compose, and OS start scripts for local startup.", "WH-US-020, WH-US-033, WH-US-042, WH-US-044, WH-US-045, WH-US-050, WH-US-052", "Primary GitHub README opened 2026-06-24; env/Docker/start-script section shows env-first configuration and startup ergonomics to keep Work Hunter aligned with."),
];

const currentQaRows = [
  qa("2026-06-24 API smoke", "WH-US-001", "PASS", "GET /api/init/status", "Init readiness returned status ok for seeded qa-root."),
  qa("2026-06-24 API smoke", "WH-US-005", "PASS", "GET /api/inbox?limit=200", "Returned 3 seeded jobs."),
  qa("2026-06-24 API smoke", "WH-US-009", "PASS", "GET /api/jobs/1", "Returned HH job detail with status new."),
  qa("2026-06-24 API smoke", "WH-US-021", "PASS", "GET /api/onboarding/questions", "Returned 12 onboarding questions."),
  qa("2026-06-24 API smoke", "WH-US-022", "PASS", "GET /api/candidate/map", "Returned candidate identity/target/facts payload."),
  qa("2026-06-24 API smoke", "WH-US-024", "PASS", "GET /api/resumes", "Returned 1 seeded resume."),
  qa("2026-06-24 API smoke", "WH-US-030", "PASS", "GET /api/events", "Returned 1 seeded calendar event."),
  qa("2026-06-24 API smoke", "WH-US-031", "PASS", "GET /api/pipeline/jobs/1", "Returned pipeline status with 1 related event."),
  qa("2026-06-24 API smoke", "WH-US-034", "PASS", "GET /api/source-status", "Returned source registry/capability status."),
  qa("2026-06-24 API smoke", "WH-US-040", "PASS", "GET /api/security/status", "Returned doctor/security status without secret exposure in summary."),
  qa("2026-06-24 API smoke", "WH-US-041", "PASS", "GET /api/replay/jobs/1", "Endpoint returned a valid replay payload; seeded job had 0 job-specific events."),
  qa("2026-06-24 API smoke", "WH-US-049", "PASS", "GET /api/stats", "Returned total_jobs=3 and source/status/score distribution."),
  qa("2026-06-24 API smoke", "WH-US-057", "PASS", "GET /api/source-setup/guide?source=hh&level=5", "Returned 4-step HH guide with goal_status blocked in no-token QA root."),
  qa("2026-06-24 API smoke", "WH-US-057", "PASS", "POST /api/source-setup/action preflight", "Whitelisted preflight action returned source=hh status=blocked and did not submit applications."),
  qa("2026-06-24 rendered smoke", "WH-US-057", "PASS", "Browser DOM: open Source Setup", "Opened http://127.0.0.1:8796, clicked Подключить источник, saw selectedSource=hh, HH.ru status, hidden HAR/L5 controls, compact collapsed log, and clean console."),
  qa("2026-06-24 rendered smoke", "WH-US-057", "PASS", "Browser DOM: run safe HH check", "Clicked Проверить вход в HH; log showed нужно действие, 1/4 готово, отправки нет; toast said Проверка готова: нужен следующий шаг; console stayed clean."),
  qa("2026-06-24 rendered smoke", "WH-US-057", "BLOCKED_TOOL", "Browser screenshot evidence", "Rendered DOM/interaction QA passed, but built-in Browser screenshot capture timed out with Page.captureScreenshot for the local tab."),
  qa("2026-06-24 API/CLI smoke 2", "WH-US-002", "PASS", "POST /api/ai/test dry_run", "Returned status=dry_run for route=smart without requiring a real provider call."),
  qa("2026-06-24 API/CLI smoke 2", "WH-US-007", "PASS", "POST /api/jobs/search-ai", "Query FastAPI returned Python Backend Engineer from the seeded job queue."),
  qa("2026-06-24 API/CLI smoke 2", "WH-US-008", "PASS", "GET /api/jobs/export?source=hh", "CSV export contained Python Backend Engineer; PowerShell Invoke-WebRequest has a host download prompt bug, so WebClient was used for verification."),
  qa("2026-06-24 API/CLI smoke 2", "WH-US-010", "PASS", "POST /api/jobs/2/status saved", "Copied QA root persisted status=saved and returned response=ok."),
  qa("2026-06-24 API/CLI smoke 2", "WH-US-011", "PASS", "POST /api/jobs/bulk hidden", "Bulk action processed 2 copied-root jobs and hidden filter returned at least 2 jobs."),
  qa("2026-06-24 API/CLI smoke 2", "WH-US-013", "PASS", "POST /api/jobs/1/note", "Saved note body round-tripped as QA note smoke 2."),
  qa("2026-06-24 API/CLI smoke 2", "WH-US-015", "PASS", "POST /api/jobs/1/letter", "Rule-based letter returned 407 chars and included seeded Acme/Python context."),
  qa("2026-06-24 API/CLI smoke 2", "WH-US-016", "PASS", "POST /api/jobs/1/letter-preview", "Preview returned campaign_letter, variants, selected_template, and use_for_campaign fields."),
  qa("2026-06-24 API/CLI smoke 2", "WH-US-017", "BLOCKED_CONFIG", "POST /api/ats-audit", "Endpoint returned HTTP 400 JSON: AI config incomplete: api_key, base_url, or model missing. This is expected without AI provider setup."),
  qa("2026-06-24 API/CLI smoke 2", "WH-US-021", "PASS", "POST /api/onboarding/answer", "Answer recorded 1 candidate fact with status=recorded."),
  qa("2026-06-24 API/CLI smoke 2", "WH-US-022", "PASS", "POST /api/candidate/facts + confirm", "Created fact_id=3 and confirm returned status=confirmed."),
  qa("2026-06-24 API/CLI smoke 2", "WH-US-023", "PASS", "POST /api/profile update", "Profile update persisted must_have_skills=FastAPI,PostgreSQL."),
  qa("2026-06-24 API/CLI smoke 2", "WH-US-025", "PASS", "POST /api/resume-variants/build", "Built variant_id=1 with ready response and nonempty body."),
  qa("2026-06-24 API/CLI smoke 2", "WH-US-026", "PASS", "POST /api/applications/build-pack", "Created application pack_id=1 with policy=ready and masked Authorization/Cookie secrets."),
  qa("2026-06-24 API/CLI smoke 2", "WH-US-027", "PASS", "POST /api/jobs/3/external-apply/dry-run", "Returned blocked_manual_review with submit=false; no external submit was attempted."),
  qa("2026-06-24 API/CLI smoke 2", "WH-US-028", "PASS", "POST /api/jobs/1/apply-plan", "Returned blocked apply plan and did not submit a real HH application."),
  qa("2026-06-24 API/CLI smoke 2", "WH-US-029", "PASS", "POST /api/jobs/1/confirm-apply confirm=false", "Returned status=blocked for missing explicit confirmation."),
  qa("2026-06-24 API/CLI smoke 2", "WH-US-032", "PASS", "POST /api/pipeline/jobs/1/prep-pack", "Prep pack returned sections, star_answers, salary_script, tech_stack, and status fields."),
  qa("2026-06-24 API/CLI smoke 2", "WH-US-032", "PASS", "POST /api/pipeline/jobs/1/event", "Scheduled follow-up event returned status=scheduled and event.id=2 on copied QA root."),
  qa("2026-06-24 API/CLI smoke 2", "WH-US-033", "PASS", "POST /api/saved-searches + GET /api/ghost-jobs", "Saved search returned ok and ghost-jobs endpoint returned an array payload."),
  qa("2026-06-24 API/CLI smoke 2", "WH-US-035", "PASS", "POST /api/sources/geekjob/test", "Returned source=geekjob with capabilities and status fields."),
  qa("2026-06-24 API/CLI smoke 2", "WH-US-036", "PASS", "POST /api/sources/hirehi/redaction-scan", "Recorded redaction evidence and masked Authorization, Cookie, and client_secret samples."),
  qa("2026-06-24 API/CLI smoke 2", "WH-US-038", "PASS", "GET /api/browser-lab/status?source=getmatch", "Returned source=getmatch browser-lab status payload."),
  qa("2026-06-24 API/CLI smoke 2", "WH-US-040", "PASS", "POST /api/init/redaction-scan", "Redaction scan masked bearer token, cookie, and client_secret samples."),
  qa("2026-06-24 API/CLI smoke 2", "WH-US-042", "PASS", "POST /api/hh/campaigns/plan", "Dry gated campaign planning created run_id=2 with 1 item and status=planned; no confirm/send was executed."),
  qa("2026-06-24 API/CLI smoke 2", "WH-US-043", "PASS", "POST /api/campaigns/external/plan", "External campaign plan blocked with reason=external_source_requires_l6_campaign_certification."),
  qa("2026-06-24 API/CLI smoke 2", "WH-US-044", "PASS", "GET /api/agent/preflight + digest", "Agent cockpit APIs returned preflight counts and digest sections without side effects."),
  qa("2026-06-24 API/CLI smoke 2", "WH-US-047", "PASS", "POST /api/templates and /api/blacklist lifecycle", "Template and blacklist save/list/delete lifecycle passed on copied QA root."),
  qa("2026-06-24 API/CLI smoke 2", "WH-US-049", "PASS", "POST /api/market-trends", "Returned nonempty market trend content for limit=3."),
  qa("2026-06-24 API/CLI smoke 2", "WH-US-051", "PASS", "CLI doctor/list/status", "doctor returned status=ok; list --limit 2 printed seeded jobs; status 1 saved updated copied-root job status."),
  qa("2026-06-24 API/CLI smoke 2", "WH-US-053", "PASS", "CLI source status + certification-matrix", "source status listed registry capabilities; certification-matrix --level 5 returned blocked matrix with missing evidence by source."),
  qa("2026-06-24 API/CLI smoke 3", "WH-US-006", "PASS", "Static JS smart-filter signals", "applySmartFilters is wired to remote/salary/level controls and renderFilteredJobs without a backend refetch."),
  qa("2026-06-24 API/CLI smoke 3", "WH-US-014", "PASS", "Static JS Telegram share signals", "shareToTelegram builds a https://t.me/share/url link via encodeURIComponent and window.open; no backend state is mutated."),
  qa("2026-06-24 API/CLI smoke 3", "WH-US-003", "PASS", "/api/init/import-wo preview+apply", "Preview returned status=preview, apply returned status=imported, AGENTS.md was written on copied QA root, and fake Bearer/api-key/Cookie secrets were masked."),
  qa("2026-06-24 API/CLI smoke 3", "WH-US-004", "PASS", "POST /api/sync score=true", "Fake collector returned geekjob status=ok count=1; scoring ran and returned scored=4 without external network."),
  qa("2026-06-24 API/CLI smoke 3", "WH-US-012", "PASS", "GET /api/jobs/1", "Returned dedicated job detail for Python Backend Engineer with status=new."),
  qa("2026-06-24 API/CLI smoke 3", "WH-US-018", "BLOCKED_CONFIG", "POST selected-job AI analysis endpoints", "ai-fit, summarize, interview-questions, and pitch returned HTTP 400 AI config incomplete, expected until an AI provider is configured."),
  qa("2026-06-24 API/CLI smoke 3", "WH-US-019", "BLOCKED_CONFIG", "fetch-full + parse/gap/smart-classify", "fetch-full returned HTTP 200 on patched local HTML; parse/gap/smart-classify surfaced AI config errors as expected without provider setup."),
  qa("2026-06-24 API/CLI smoke 3", "WH-US-020", "BLOCKED_CONFIG", "POST /api/chat", "Returned HTTP 400 with AI config incomplete; chat UI path requires provider setup before functional AI replies."),
  qa("2026-06-24 API/CLI smoke 3", "WH-US-037", "PASS", "External target + HAR configure APIs", "Direct target and HAR-derived target both returned status=configured; fake Authorization/Cookie/resume secrets were masked."),
  qa("2026-06-24 API/CLI smoke 3", "WH-US-039", "PASS", "Browser Lab map/dry-run/execute-dry-run", "API smoke returned submit=false for dry-run and execute-dry-run; targeted browser-lab tests passed 3/3 for mapping, executor, and web API no-submit behavior."),
  qa("2026-06-24 API/CLI smoke 3", "WH-US-045", "PASS", "Agent run/status/cancel APIs", "agent/run digest returned an operation id, operation-status loaded, and cancelling a seeded running operation returned status=cancelled."),
  qa("2026-06-24 API/CLI smoke 3", "WH-US-046", "PASS", "Approval modify/approve/reject/flag APIs", "Four seeded approvals returned statuses modified, approved, rejected, and flagged."),
  qa("2026-06-24 API/CLI smoke 3", "WH-US-048", "PASS", "HH API Lab quick/custom/snippet APIs", "Quick calls listed me/resumes/negotiations/vacancies; fake /me call returned status=ok with secrets masked; snippet save/list/delete passed."),
  qa("2026-06-24 API/CLI smoke 3", "WH-US-050", "BLOCKED_CONFIG", "POST /api/market-trends", "Endpoint returned HTTP 200 with inline AI config incomplete message; trend generation awaits AI provider setup."),
  qa("2026-06-24 API/CLI smoke 3", "WH-US-052", "PASS", "CLI hh-auth-status/account/resumes", "hh-auth-status, hh-account set-token, and hh-resumes --json exited 0; fake token/client_secret were masked."),
  qa("2026-06-24 API/CLI smoke 3", "WH-US-054", "PASS", "CLI api-recon/external-session/adapter-plan", "api-recon-har, external-session import/list/show, and external-adapter-plan exited 0 with no hits for fake Authorization/Cookie/access_token/resume_id secrets after the redaction fix."),
  qa("2026-06-24 AI route retest", "WH-US-017", "PASS", "Default-route AI service retest", "Fixed legacy direct/backend gap: ATS audit now uses the configured default AI route when direct API fields are empty; targeted AI/UI tests passed 14/14."),
  qa("2026-06-24 AI route retest", "WH-US-018", "PASS", "Default-route AI service retest", "ai_fit plus related selected-job AI actions are covered by default-route chat_completion fallback; targeted AI/UI tests passed 14/14."),
  qa("2026-06-24 AI route retest", "WH-US-019", "PASS", "Default-route AI service retest", "parse_job_structure, gap_analysis, and smart_classify now use the configured default AI route when legacy direct is empty; targeted AI/UI tests passed 14/14."),
  qa("2026-06-24 AI route retest", "WH-US-020", "PASS", "Default-route AI service retest", "Chat now uses the configured default AI route when legacy direct is empty; targeted AI/UI tests passed 14/14."),
  qa("2026-06-24 AI route retest", "WH-US-050", "PASS", "Default-route AI service retest", "Market trends now use the configured default AI route when legacy direct is empty; targeted AI/UI tests passed 14/14."),
  qa("2026-06-24 AI setup UX retest", "WH-US-001", "PASS", "Setup AI route guidance", "Setup now shows the default AI route note, explaining that AI buttons use it when direct API fields are empty; static UI/mojibake tests passed."),
  qa("2026-06-24 Source Setup final retest", "WH-US-057", "PASS", "Wizard DOM/API/full-suite evidence", "Source Setup Wizard remains accepted on DOM interaction, API whitelist, static UI, and full pytest evidence; screenshot timeout is tracked as a Browser tool limitation, not a product failure."),
  qa("2026-06-24 targeted pytest", "WH-US-055", "PASS", "MCP surface/safety suites", "pytest tests/test_mcp_surface.py tests/test_mcp_safety.py tests/test_hh_agent_mcp.py plus safety group passed 42 tests."),
  qa("2026-06-24 targeted pytest", "WH-US-056", "PASS", "Security/redaction/scheduler suites", "pytest tests/test_security_redaction.py tests/test_scheduler.py tests/test_agent_orchestrator.py within the 42-test targeted run passed; repeated Windows pytest-current cleanup warning is external to assertions."),
];

const uiViews = parseUiViews(html);
const uiControls = parseUiControls(html);
const jsApis = parseJsApis(js);
const serverRoutes = parseServerRoutes(server);
const serviceMethods = parseServiceMethods(services);
const cliCommands = parseCliCommands(cli);
const tests = await parseTests(path.join(projectRoot, "tests"));
const cssSignals = parseCssSignals(css);

for (const row of featureRows) {
  const override = qaOverrides[row["Feature ID"]] || {};
  Object.assign(row, override);
}

const workbook = Workbook.create();
const summary = workbook.worksheets.add("Summary");
const ledger = workbook.worksheets.add("Feature Ledger");
const errors = workbook.worksheets.add("Error Log");
const apiRoutes = workbook.worksheets.add("API Routes");
const uiSheet = workbook.worksheets.add("UI Controls");
const serviceSheet = workbook.worksheets.add("Service Methods");
const cliSheet = workbook.worksheets.add("CLI Commands");
const testSheet = workbook.worksheets.add("Test Evidence");
const cssSheet = workbook.worksheets.add("CSS Signals");
const referenceSheet = workbook.worksheets.add("Reference Baseline");
const currentQaSheet = workbook.worksheets.add("Current QA Batch");

writeSummary(summary, featureRows, serverRoutes, uiViews, uiControls, serviceMethods, cliCommands, tests, cssSignals, referenceRows, currentQaRows);
writeTable(ledger, "FeatureLedgerTable", featureRows, [
  "Feature ID", "Area", "Surface", "User Story", "Expected Behavior", "Code Evidence", "API or Command", "Test Evidence", "Risk", "Status", "Initial Test Result", "Error or Issue", "Fix Status", "Retest Result", "Notes", "Last Updated",
]);
writeTable(errors, "ErrorLogTable", errorRows(featureRows), [
  "Feature ID", "Area", "Risk", "Status", "Initial Test Result", "Error or Issue", "Fix Status", "Retest Result", "Notes",
]);
writeTable(apiRoutes, "ApiRoutesTable", serverRoutes.concat(jsApis), [
  "Source", "Method", "Route", "Handler", "Line", "Evidence",
]);
writeTable(uiSheet, "UiControlsTable", uiViews.concat(uiControls), [
  "Kind", "View", "Label or ID", "Line", "Evidence",
]);
writeTable(serviceSheet, "ServiceMethodsTable", serviceMethods, [
  "Class", "Method", "Line", "Evidence",
]);
writeTable(cliSheet, "CliCommandsTable", cliCommands, [
  "Command", "Line", "Evidence",
]);
writeTable(testSheet, "TestEvidenceTable", tests, [
  "Test File", "Test Name", "Line", "Evidence",
]);
writeTable(cssSheet, "CssSignalsTable", cssSignals, [
  "Kind", "Selector or Token", "Line", "Evidence",
]);
writeTable(referenceSheet, "ReferenceBaselineTable", referenceRows, [
  "Reference", "URL", "Commit", "Baseline Behavior", "Tracked User Stories", "Notes",
]);
writeTable(currentQaSheet, "CurrentQaBatchTable", currentQaRows, [
  "Batch", "Story", "Result", "Check", "Evidence",
]);

formatWorkbook(workbook);

const previewRanges = {
  "Summary": "A1:H20",
  "Feature Ledger": "A1:P20",
  "Error Log": "A1:I20",
  "API Routes": "A1:F30",
  "UI Controls": "A1:E30",
  "Service Methods": "A1:D30",
  "CLI Commands": "A1:C30",
  "Test Evidence": "A1:D30",
  "CSS Signals": "A1:D25",
  "Reference Baseline": "A1:F20",
  "Current QA Batch": "A1:E95",
};

for (const [sheetName, range] of Object.entries(previewRanges)) {
  const preview = await workbook.render({ sheetName, range, scale: 1, format: "png" });
  const previewPath = path.join(outputDir, `${sheetName.replaceAll(" ", "_").toLowerCase()}.png`);
  try {
    await fs.writeFile(previewPath, new Uint8Array(await preview.arrayBuffer()));
  } catch (err) {
    console.warn(JSON.stringify({
      kind: "warning",
      message: "Preview image write skipped; file may be open in another app.",
      sheetName,
      path: previewPath,
      error: String(err?.message || err),
    }));
  }
}

const formulaErrors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 300 },
  summary: "final formula error scan",
});
console.log(formulaErrors.ndjson);

const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(workbookPath);
console.log(workbookPath);

function story(id, area, surface, userStory, expected, code, api, tests, risk) {
  return {
    "Feature ID": id,
    Area: area,
    Surface: surface,
    "User Story": userStory,
    "Expected Behavior": expected,
    "Code Evidence": code,
    "API or Command": api,
    "Test Evidence": tests,
    Risk: risk,
    Status: "Inventory complete - pending QA",
    "Initial Test Result": "Not run",
    "Error or Issue": "",
    "Fix Status": "",
    "Retest Result": "",
    Notes: "",
    "Last Updated": today,
  };
}

function reference(name, url, commit, baseline, stories, notes) {
  return {
    Reference: name,
    URL: url,
    Commit: commit,
    "Baseline Behavior": baseline,
    "Tracked User Stories": stories,
    Notes: notes,
  };
}

function qa(batch, story, result, check, evidence) {
  return {
    Batch: batch,
    Story: story,
    Result: result,
    Check: check,
    Evidence: evidence,
  };
}

async function loadJson(file) {
  try {
    return JSON.parse(await fs.readFile(file, "utf8"));
  } catch {
    return {};
  }
}

function lineOf(text, needle) {
  const index = text.indexOf(needle);
  if (index < 0) return "";
  return String(text.slice(0, index).split(/\r?\n/).length);
}

function parseUiViews(source) {
  const rows = [];
  const navRegex = /<button[^>]*data-view="([^"]+)"[^>]*>([\s\S]*?)<\/button>/g;
  let match;
  while ((match = navRegex.exec(source))) {
    const label = stripTags(match[2]);
    rows.push({
      Kind: "View",
      View: match[1],
      "Label or ID": label,
      Line: lineOf(source, match[0]),
      Evidence: `work_hunter/web/static/index.html:${lineOf(source, match[0])}`,
    });
  }
  return rows;
}

function parseUiControls(source) {
  const rows = [];
  const controlRegex = /<(button|input|select|textarea)\b([^>]*)>/g;
  let match;
  while ((match = controlRegex.exec(source))) {
    const attrs = match[2];
    const idMatch = attrs.match(/id="([^"]+)"/);
    const id = idMatch ? idMatch[1] : "";
    if (!id) continue;
    const before = source.slice(0, match.index);
    const viewMatch = [...before.matchAll(/<section id="view-([^"]+)"/g)].pop();
    let label = id;
    if (match[1] === "button" || match[1] === "textarea" || match[1] === "select") {
      const closeTag = `</${match[1]}>`;
      const closeIndex = source.indexOf(closeTag, controlRegex.lastIndex);
      if (closeIndex >= 0) {
        label = stripTags(source.slice(controlRegex.lastIndex, closeIndex)).trim() || id;
      }
    }
    rows.push({
      Kind: match[1],
      View: viewMatch ? viewMatch[1] : "global",
      "Label or ID": label === id ? id : `${id} - ${label}`,
      Line: String(before.split(/\r?\n/).length),
      Evidence: `work_hunter/web/static/index.html:${before.split(/\r?\n/).length}`,
    });
  }
  return rows;
}

function parseJsApis(source) {
  const rows = [];
  const patterns = [
    /api\(`([^`]+)`/g,
    /api\("([^"]+)"/g,
    /api\('([^']+)'/g,
  ];
  const seen = new Set();
  for (const pattern of patterns) {
    let match;
    while ((match = pattern.exec(source))) {
      if (seen.has(match[1])) continue;
      seen.add(match[1]);
      const line = lineOf(source, match[0]);
      rows.push({
        Source: "Frontend api()",
        Method: inferMethod(source, match.index),
        Route: match[1],
        Handler: "work_hunter/web/static/app.js",
        Line: line,
        Evidence: `work_hunter/web/static/app.js:${line}`,
      });
    }
  }
  return rows.sort((a, b) => a.Route.localeCompare(b.Route));
}

function parseServerRoutes(source) {
  const rows = [];
  let currentMethod = "";
  source.split(/\r?\n/).forEach((line, index) => {
    if (line.includes("def do_GET")) currentMethod = "GET";
    if (line.includes("def do_POST")) currentMethod = "POST";
    const routeMatches = [
      ...line.matchAll(/path == "([^"]+)"/g),
      ...line.matchAll(/path in \{([^}]+)\}/g),
      ...line.matchAll(/path\.startswith\("([^"]+)"\)/g),
    ];
    for (const match of routeMatches) {
      if (match[0].includes("path in")) {
        const routes = [...match[1].matchAll(/"([^"]+)"/g)].map((m) => m[1]);
        for (const route of routes) {
          rows.push(routeRow(currentMethod, route, line, index + 1, "server"));
        }
      } else {
        rows.push(routeRow(currentMethod, match[1], line, index + 1, "server"));
      }
    }
  });
  return rows;
}

function routeRow(method, route, line, lineNumber, source) {
  return {
    Source: source === "server" ? "Server route" : source,
    Method: method || "ANY",
    Route: route,
    Handler: "work_hunter/web/server.py",
    Line: String(lineNumber),
    Evidence: `work_hunter/web/server.py:${lineNumber} ${line.trim()}`,
  };
}

function inferMethod(source, index) {
  const window = source.slice(index, index + 220);
  const match = window.match(/method:\s*"([^"]+)"/);
  return match ? match[1] : "GET";
}

function parseServiceMethods(source) {
  const rows = [];
  const classIndex = source.indexOf("class WorkHunter");
  if (classIndex < 0) return rows;
  const serviceSource = source.slice(classIndex);
  const methodRegex = /^\s{4}def ([A-Za-z_][A-Za-z0-9_]*)\(/gm;
  let match;
  while ((match = methodRegex.exec(serviceSource))) {
    if (match[1].startsWith("_")) continue;
    const absoluteIndex = classIndex + match.index;
    const line = source.slice(0, absoluteIndex).split(/\r?\n/).length;
    rows.push({
      Class: "WorkHunter",
      Method: match[1],
      Line: String(line),
      Evidence: `work_hunter/services.py:${line}`,
    });
  }
  return rows;
}

function parseCliCommands(source) {
  const rows = [];
  const regex = /add_parser\("([^"]+)"/g;
  let match;
  while ((match = regex.exec(source))) {
    const line = lineOf(source, match[0]);
    rows.push({
      Command: match[1],
      Line: line,
      Evidence: `work_hunter/cli.py:${line}`,
    });
  }
  return rows;
}

async function parseTests(testDir) {
  const entries = await fs.readdir(testDir);
  const rows = [];
  for (const entry of entries.filter((name) => name.endsWith(".py")).sort()) {
    const file = path.join(testDir, entry);
    const text = await readText(file);
    const regex = /^def (test_[A-Za-z0-9_]+)/gm;
    let match;
    while ((match = regex.exec(text))) {
      const line = text.slice(0, match.index).split(/\r?\n/).length;
      rows.push({
        "Test File": `tests/${entry}`,
        "Test Name": match[1],
        Line: String(line),
        Evidence: `tests/${entry}:${line}`,
      });
    }
  }
  return rows;
}

function parseCssSignals(source) {
  const rows = [];
  const selectors = [
    ".sidebar", ".nav-button", ".view", ".workspace", ".job-list", ".detail",
    ".panel", ".filters", ".bulk-toolbar", ".ai-tabs", ".agent-tabs",
    ".agent-json", ".ui-error-line", "[data-theme=\"dark\"]", "@media (max-width: 980px)",
    "@media (max-width: 640px)",
  ];
  for (const selector of selectors) {
    const line = lineOf(source, selector);
    if (!line) continue;
    rows.push({
      Kind: selector.startsWith("@media") ? "Responsive" : "Selector",
      "Selector or Token": selector,
      Line: line,
      Evidence: `work_hunter/web/static/app.css:${line}`,
    });
  }
  return rows;
}

function stripTags(value) {
  return value.replace(/<[^>]+>/g, "").replace(/\s+/g, " ").trim();
}

function errorRows(rows) {
  const errors = rows.filter((row) => row["Error or Issue"] || /^(fail|blocked|error)/i.test(row["Initial Test Result"] || ""));
  if (errors.length) return errors.map((row) => pick(row, ["Feature ID", "Area", "Risk", "Status", "Initial Test Result", "Error or Issue", "Fix Status", "Retest Result", "Notes"]));
  return [{
    "Feature ID": "None yet",
    Area: "",
    Risk: "",
    Status: "No errors documented in current ledger",
    "Initial Test Result": "",
    "Error or Issue": "",
    "Fix Status": "",
    "Retest Result": "",
    Notes: "",
  }];
}

function pick(row, keys) {
  return Object.fromEntries(keys.map((key) => [key, row[key] ?? ""]));
}

function writeSummary(sheet, rows, routes, views, controls, methods, commands, tests, cssRows, references, currentQa) {
  sheet.getRange("A1:H1").values = [["Work Hunter Feature QA Ledger", "", "", "", "", "", "", ""]];
  sheet.getRange("A2:H2").values = [[`Generated ${today}`, "", "", "", "", "", "", ""]];
  const metrics = [
    ["Feature stories", rows.length],
    ["UI views", views.length],
    ["UI controls", controls.length],
    ["API references/routes", routes.length],
    ["Service methods", methods.length],
    ["CLI parser entries", commands.length],
    ["Test functions", tests.length],
    ["CSS/responsive signals", cssRows.length],
    ["Reference baselines", references.length],
    ["Current QA checks", currentQa.length],
  ];
  sheet.getRange("A4:B13").values = metrics;
  const byStatus = countBy(rows, "Status");
  const statusRows = Object.entries(byStatus).map(([status, count]) => [status, count]);
  sheet.getRangeByIndexes(3, 3, 1, 2).values = [["Status", "Count"]];
  sheet.getRangeByIndexes(4, 3, Math.max(statusRows.length, 1), 2).values = statusRows.length ? statusRows : [["No statuses", 0]];
  const riskRows = Object.entries(countBy(rows, "Risk")).map(([risk, count]) => [risk, count]);
  sheet.getRangeByIndexes(3, 6, 1, 2).values = [["Risk", "Count"]];
  sheet.getRangeByIndexes(4, 6, Math.max(riskRows.length, 1), 2).values = riskRows.length ? riskRows : [["No risk", 0]];
  sheet.getRange("A14:H14").values = [["Operating Rule", "", "", "", "", "", "", ""]];
  sheet.getRange("A15:H18").values = [
    ["This workbook is the single canonical tracking artifact for the active goal.", "", "", "", "", "", "", ""],
    ["Feature status should be updated here after automated tests, rendered browser QA, fixes, and retests.", "", "", "", "", "", "", ""],
    ["Rows are grouped as user-facing stories. Raw API/UI/CLI/test evidence is preserved on supporting sheets.", "", "", "", "", "", "", ""],
    ["External-auth or real-apply behavior should be tested through safe dry-run/blocked states unless explicit real credentials and confirmation are provided.", "", "", "", "", "", "", ""],
  ];
}

function countBy(rows, key) {
  const counts = {};
  for (const row of rows) {
    counts[row[key]] = (counts[row[key]] || 0) + 1;
  }
  return counts;
}

function writeTable(sheet, tableName, rows, headers) {
  const safeRows = rows.length ? rows : [Object.fromEntries(headers.map((header) => [header, ""]))];
  sheet.getRangeByIndexes(0, 0, 1, headers.length).values = [headers];
  const matrix = safeRows.map((row) => headers.map((header) => normalizeCell(row[header])));
  sheet.getRangeByIndexes(1, 0, matrix.length, headers.length).values = matrix;
  const endCol = columnName(headers.length);
  const table = sheet.tables.add(`A1:${endCol}${matrix.length + 1}`, true, tableName);
  table.showFilterButton = true;
}

function normalizeCell(value) {
  if (value == null) return "";
  const text = typeof value === "string" ? value : JSON.stringify(value);
  return text.length > 32000 ? `${text.slice(0, 31900)}...` : text;
}

function columnName(count) {
  let n = count;
  let name = "";
  while (n > 0) {
    const rem = (n - 1) % 26;
    name = String.fromCharCode(65 + rem) + name;
    n = Math.floor((n - 1) / 26);
  }
  return name;
}

function formatWorkbook(workbook) {
  for (const sheet of workbook.worksheets.items) {
    const used = sheet.getUsedRange();
    if (!used) continue;
    sheet.showGridLines = false;
    sheet.freezePanes.freezeRows(1);
    used.format.font = { name: "Aptos", size: 10, color: "#111827" };
    used.format.wrapText = true;
    used.format.autofitColumns();
    used.format.autofitRows();
    const header = sheet.getRangeByIndexes(0, 0, 1, used.columnCount);
    header.format = {
      fill: "#111827",
      font: { bold: true, color: "#FFFFFF" },
      wrapText: true,
    };
    header.format.rowHeightPx = 34;
  }

  const ledger = workbook.worksheets.getItem("Feature Ledger");
  ledger.freezePanes.freezeRows(1);
  ledger.getRange("A:A").format.columnWidthPx = 96;
  ledger.getRange("B:C").format.columnWidthPx = 110;
  ledger.getRange("D:E").format.columnWidthPx = 340;
  ledger.getRange("F:H").format.columnWidthPx = 260;
  ledger.getRange("I:I").format.columnWidthPx = 80;
  ledger.getRange("J:O").format.columnWidthPx = 180;
  ledger.getRange("P:P").format.columnWidthPx = 100;
  ledger.getRange("J2:J300").dataValidation = { rule: { type: "list", values: ["Inventory complete - pending QA", "Passed automated", "Passed rendered QA", "Failed - needs fix", "Blocked external dependency", "Fixed - pending retest", "Passed retest"] } };
  ledger.getRange("I2:I300").dataValidation = { rule: { type: "list", values: ["Low", "Medium", "High", "Critical"] } };
  ledger.getRange("J2:J300").conditionalFormats.add("containsText", { text: "Failed", format: { fill: "#FEE2E2", font: { color: "#991B1B" } } });
  ledger.getRange("J2:J300").conditionalFormats.add("containsText", { text: "Blocked", format: { fill: "#FEF3C7", font: { color: "#92400E" } } });
  ledger.getRange("J2:J300").conditionalFormats.add("containsText", { text: "Passed", format: { fill: "#DCFCE7", font: { color: "#166534" } } });
  ledger.getUsedRange().format.autofitRows();

  const errorSheet = workbook.worksheets.getItem("Error Log");
  errorSheet.getRange("A:A").format.columnWidthPx = 96;
  errorSheet.getRange("B:C").format.columnWidthPx = 80;
  errorSheet.getRange("D:D").format.columnWidthPx = 130;
  errorSheet.getRange("E:E").format.columnWidthPx = 260;
  errorSheet.getRange("F:F").format.columnWidthPx = 420;
  errorSheet.getRange("G:G").format.columnWidthPx = 220;
  errorSheet.getRange("H:I").format.columnWidthPx = 320;
  errorSheet.getUsedRange().format.autofitRows();

  const summary = workbook.worksheets.getItem("Summary");
  summary.getRange("A1:H1").merge();
  summary.getRange("A2:H2").merge();
  summary.getRange("A14:H14").merge();
  summary.getRange("A15:H18").merge(true);
  summary.getRange("A1").format = { fill: "#0F766E", font: { bold: true, color: "#FFFFFF", size: 16 } };
  summary.getRange("A2").format = { fill: "#CCFBF1", font: { color: "#134E4A" } };
  summary.getRange("A4:B13").format.borders = { preset: "outside", style: "thin", color: "#CBD5E1" };
  summary.getRange("D4:E20").format.borders = { preset: "outside", style: "thin", color: "#CBD5E1" };
  summary.getRange("G4:H20").format.borders = { preset: "outside", style: "thin", color: "#CBD5E1" };
  summary.getRange("A14").format = { fill: "#111827", font: { bold: true, color: "#FFFFFF" } };
  summary.getRange("A15:H18").format = { fill: "#F8FAFC", wrapText: true };
  summary.getRange("A:A").format.columnWidthPx = 210;
  summary.getRange("B:B").format.columnWidthPx = 90;
  summary.getRange("D:D").format.columnWidthPx = 240;
  summary.getRange("G:G").format.columnWidthPx = 120;

  const referenceSheet = workbook.worksheets.getItem("Reference Baseline");
  referenceSheet.getRange("A:A").format.columnWidthPx = 180;
  referenceSheet.getRange("B:B").format.columnWidthPx = 290;
  referenceSheet.getRange("C:C").format.columnWidthPx = 290;
  referenceSheet.getRange("D:D").format.columnWidthPx = 520;
  referenceSheet.getRange("E:F").format.columnWidthPx = 360;
  referenceSheet.getUsedRange().format.autofitRows();

  const currentQaSheet = workbook.worksheets.getItem("Current QA Batch");
  currentQaSheet.getRange("A:A").format.columnWidthPx = 170;
  currentQaSheet.getRange("B:C").format.columnWidthPx = 120;
  currentQaSheet.getRange("D:D").format.columnWidthPx = 300;
  currentQaSheet.getRange("E:E").format.columnWidthPx = 520;
  currentQaSheet.getRange("C2:C300").conditionalFormats.add("containsText", { text: "PASS", format: { fill: "#DCFCE7", font: { color: "#166534" } } });
  currentQaSheet.getRange("C2:C300").conditionalFormats.add("containsText", { text: "BLOCKED", format: { fill: "#FEF3C7", font: { color: "#92400E" } } });
  currentQaSheet.getRange("C2:C300").conditionalFormats.add("containsText", { text: "FAIL", format: { fill: "#FEE2E2", font: { color: "#991B1B" } } });
  currentQaSheet.getUsedRange().format.autofitRows();
}
