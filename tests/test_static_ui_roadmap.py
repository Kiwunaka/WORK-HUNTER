from __future__ import annotations

from pathlib import Path


STATIC = Path("work_hunter/web/static")


def test_static_ui_exposes_roadmap_views_and_controls():
    html = (STATIC / "index.html").read_text(encoding="utf-8")

    for view in [
        "inbox",
        "setup",
        "onboarding",
        "candidate-map",
        "resumes",
        "job-detail",
        "application-preview",
        "campaigns",
        "pipeline",
        "interview-prep",
        "replay",
        "audit-security",
    ]:
        assert f'data-view="{view}"' in html
        assert f'id="view-{view}"' in html

    for control_id in [
        "ui-error-line",
        "jobs-body",
        "source-filter",
        "min-score-filter",
        "setup-summary",
        "ai-readiness-list",
        "onboarding-questions",
        "candidate-facts-list",
        "resume-variant-output",
        "job-detail-id-input",
        "job-detail-load-button",
        "job-detail-output",
        "application-preview-output",
        "campaign-runs-list",
        "pipeline-status-output",
        "interview-prep-job-id",
        "interview-prep-stage-select",
        "interview-prep-pack-button",
        "interview-prep-output",
        "replay-timeline-list",
        "audit-security-output",
    ]:
        assert f'id="{control_id}"' in html


def test_static_ui_has_roadmap_js_functions_and_event_hooks():
    js = (STATIC / "app.js").read_text(encoding="utf-8")

    for fn in [
        "setUiError",
        "loadJobs",
        "renderJobs",
        "loadSetupStatus",
        "renderAiReadiness",
        "testAiRoute",
        "loadOnboardingQuestions",
        "submitOnboardingAnswer",
        "loadCandidateMap",
        "confirmCandidateFact",
        "buildResumeVariant",
        "loadJobDetailView",
        "buildApplicationPreview",
        "loadCampaignRuns",
        "planHhCampaign",
        "loadPipelineStatus",
        "buildPipelinePrepPack",
        "buildInterviewPrepPack",
        "loadReplayTimeline",
        "loadSecurityStatus",
    ]:
        assert f"function {fn}" in js or f"function {fn}(" in js or f"async function {fn}" in js

    for event_id in [
        "refresh-button",
        "source-filter",
        "min-score-filter",
        "setup-refresh-button",
        "ai-test-button",
        "onboarding-submit-button",
        "candidate-refresh-button",
        "resume-variant-button",
        "job-detail-load-button",
        "application-preview-button",
        "campaign-plan-button",
        "pipeline-refresh-button",
        "pipeline-prep-pack-button",
        "interview-prep-pack-button",
        "replay-refresh-button",
        "audit-security-refresh-button",
    ]:
        assert f'$("#{event_id}")' in js

    assert "/api/inbox?" in js


def test_static_ui_bulk_checkboxes_do_not_change_row_selection_or_lose_state():
    js = (STATIC / "app.js").read_text(encoding="utf-8")

    render_jobs = js.split("function renderJobs()", 1)[1].split("async function selectJob", 1)[0]
    render_filtered = js.split("function renderFilteredJobs", 1)[1].split("function updateSummaryForFiltered", 1)[0]

    for block in [render_jobs, render_filtered]:
        assert 'onclick="event.stopPropagation();toggleJobSelect(${job.id}, this.checked)"' in block
        assert '${selectedJobIds.has(job.id) ? "checked" : ""}' in block


def test_static_ui_exposes_dedicated_job_detail_screen():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    js = (STATIC / "app.js").read_text(encoding="utf-8")

    assert 'data-view="job-detail"' in html
    assert 'id="view-job-detail"' in html
    detail_section = html.split('id="view-job-detail"', 1)[1].split("</section>", 1)[0]
    for control_id in [
        "job-detail-id-input",
        "job-detail-load-button",
        "job-detail-output",
    ]:
        assert f'id="{control_id}"' in detail_section

    assert "function renderJobDetailHtml" in js
    assert "async function loadJobDetailView" in js
    assert "job-detail-id-input" in js
    assert '$("#job-detail-load-button")' in js
    assert "data-view='job-detail'" in js


def test_static_ui_exposes_dedicated_interview_prep_screen():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    js = (STATIC / "app.js").read_text(encoding="utf-8")

    assert 'data-view="interview-prep"' in html
    assert 'id="view-interview-prep"' in html
    prep_section = html.split('id="view-interview-prep"', 1)[1].split("</section>", 1)[0]
    for control_id in [
        "interview-prep-job-id",
        "interview-prep-stage-select",
        "interview-prep-pack-button",
        "interview-prep-output",
    ]:
        assert f'id="{control_id}"' in prep_section

    assert "async function buildInterviewPrepPack" in js
    assert "interview-prep-job-id" in js
    assert "interview-prep-stage-select" in js
    assert '$("#interview-prep-pack-button")' in js
    assert "data-view='interview-prep'" in js
    assert "/prep-pack" in js


def test_static_assets_are_valid_utf8_without_mojibake_markers():
    markers = [
        "\ufffd",
        "\u00d0",
        "\u00d1",
        "\u00e2\u20ac",
        "Рџ",
        "Р’",
        "Р°",
        "СЃ",
        "вЂ",
        "в†",
        "В·",
        "рџ",
    ]

    for path in [
        STATIC / "index.html",
        STATIC / "app.js",
        STATIC / "app.css",
    ]:
        text = path.read_text(encoding="utf-8")
        for marker in markers:
            assert marker not in text, f"{path} contains mojibake marker {marker!r}"


def test_static_ui_exposes_ai_readiness_and_error_ux():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    js = (STATIC / "app.js").read_text(encoding="utf-8")

    assert 'id="ui-error-line"' in html
    assert 'id="ai-readiness-list"' in html
    assert 'id="ai-default-route-note"' in html
    assert "function setUiError" in js
    assert "function renderAiReadiness" in js
    assert "/api/ai/status" in js
    assert "Основной AI-маршрут" in js
    assert "Маршруты AI не настроены" in js


def test_static_ui_exposes_setup_wo_import_controls():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    js = (STATIC / "app.js").read_text(encoding="utf-8")

    setup_section = html.split('id="view-setup"', 1)[1].split("</section>", 1)[0]
    for control_id in [
        "setup-import-wo-source",
        "setup-import-wo-preview-button",
        "setup-import-wo-apply-button",
        "setup-import-wo-output",
    ]:
        assert f'id="{control_id}"' in setup_section

    assert "async function previewSetupWoImport" in js
    assert "async function applySetupWoImport" in js
    assert "/api/init/import-wo/preview" in js
    assert "/api/init/import-wo" in js
    assert '$("#setup-import-wo-preview-button")' in js
    assert '$("#setup-import-wo-apply-button")' in js


def test_static_ui_exposes_resume_import_and_variant_diff_controls():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    js = (STATIC / "app.js").read_text(encoding="utf-8")

    assert 'data-view="resumes"' in html
    assert 'id="view-resumes"' in html
    resumes_section = html.split('id="view-resumes"', 1)[1].split("</section>", 1)[0]
    for control_id in [
        "resume-import-path",
        "resume-import-button",
        "resume-import-output",
        "resumes-list",
        "resume-variant-diff",
    ]:
        assert f'id="{control_id}"' in html

    for control_id in [
        "resume-import-path",
        "resume-import-button",
        "resume-import-output",
        "resumes-list",
    ]:
        assert f'id="{control_id}"' in resumes_section

    assert "async function importResume" in js
    assert "async function loadResumes" in js
    assert "function renderResumeVariantDiff" in js
    assert '$("#resume-import-button")' in js
    assert '$("#resume-variant-diff")' in js
    assert "data-view='resumes'" in js


def test_static_ui_exposes_source_readiness_badges():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    js = (STATIC / "app.js").read_text(encoding="utf-8")

    assert 'id="source-readiness-list"' in html
    assert "/api/source-status" in js
    assert "readiness_badge" in js
    assert "adapter_status" in js


def test_static_ui_exposes_source_sync_and_test_actions():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    js = (STATIC / "app.js").read_text(encoding="utf-8")

    sources_section = html.split('id="view-sources"', 1)[1].split("</section>", 1)[0]
    for control_id in [
        "source-action-source",
        "source-action-limit",
        "source-sync-button",
        "source-test-button",
        "source-action-output",
    ]:
        assert f'id="{control_id}"' in sources_section

    assert 'id="source-sync-button" class="primary" data-label="Sync"' in sources_section
    assert 'id="source-test-button" data-label="Test"' in sources_section
    assert "function selectedSourceActionName" in js
    assert "async function syncSelectedSource" in js
    assert "async function testSelectedSource" in js
    assert "/sync" in js
    assert "/test" in js
    assert '$("#source-sync-button")' in js
    assert '$("#source-test-button")' in js


def test_static_ui_exposes_source_certification_matrix():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    js = (STATIC / "app.js").read_text(encoding="utf-8")

    assert 'id="source-certification-level"' in html
    assert '<option value="6">L6</option>' in html
    assert 'id="source-certification-summary"' in html
    assert 'id="source-certification-matrix"' in html
    assert 'id="source-certification-plan-button"' in html
    assert 'id="source-certification-plan-output"' in html
    assert "/api/sources/certification-matrix" in js
    assert "/api/sources/certification-plan" in js
    assert "function sourceCertificationLevel" in js
    assert "function renderSourceCertificationMatrix" in js
    assert "async function loadSourceCertificationPlan" in js
    assert "missing_by_source" in js
    assert "promotion_payloads" in js
    assert '$("#source-certification-plan-button")' in js


def test_static_ui_exposes_source_certification_evidence_recorder():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    js = (STATIC / "app.js").read_text(encoding="utf-8")

    for control_id in [
        "source-certification-source",
        "source-certification-evidence-json",
        "source-certification-evidence-button",
        "source-certification-evidence-output",
    ]:
        assert f'id="{control_id}"' in html

    assert "async function recordSourceCertificationEvidence" in js
    assert "/certification-evidence" in js
    assert "level: sourceCertificationLevel()" in js
    assert '$("#source-certification-evidence-button")' in js


def test_static_ui_exposes_source_certification_promotion_control():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    js = (STATIC / "app.js").read_text(encoding="utf-8")

    for control_id in [
        "source-certification-promote-source",
        "source-certification-promote-button",
        "source-certification-promote-output",
    ]:
        assert f'id="{control_id}"' in html

    assert "async function promoteSourceCertification" in js
    assert "/certify" in js
    assert "level: sourceCertificationLevel()" in js
    assert '$("#source-certification-promote-button")' in js
    assert "renderActionError(output, err" in js


def test_static_ui_exposes_external_apply_target_configurator():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    js = (STATIC / "app.js").read_text(encoding="utf-8")

    for control_id in [
        "source-external-target-source",
        "source-external-target-session",
        "source-external-target-url",
        "source-external-target-method",
        "source-external-target-payload-template",
        "source-external-target-button",
        "source-external-target-output",
    ]:
        assert f'id="{control_id}"' in html

    assert "async function configureSourceExternalApplyTarget" in js
    assert "/external-apply-target" in js
    assert "level: sourceCertificationLevel()" in js
    assert '$("#source-external-target-button")' in js


def test_static_ui_exposes_external_apply_from_har_configurator():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    js = (STATIC / "app.js").read_text(encoding="utf-8")

    for control_id in [
        "source-external-har-source",
        "source-external-har-path",
        "source-external-har-hosts",
        "source-external-har-button",
        "source-external-har-output",
    ]:
        assert f'id="{control_id}"' in html

    assert "async function configureSourceExternalApplyFromHar" in js
    assert "/external-apply-from-har" in js
    assert "level: sourceCertificationLevel()" in js
    assert '$("#source-external-har-button")' in js
    assert "renderActionError(output, err" in js


def test_static_ui_exposes_source_redaction_scan_recorder():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    js = (STATIC / "app.js").read_text(encoding="utf-8")

    for control_id in [
        "source-redaction-scan-source",
        "source-redaction-scan-payload",
        "source-redaction-scan-text",
        "source-redaction-scan-button",
        "source-redaction-scan-output",
    ]:
        assert f'id="{control_id}"' in html

    assert "async function recordSourceRedactionScan" in js
    assert "/redaction-scan" in js
    assert '$("#source-redaction-scan-button")' in js


def test_static_ui_exposes_replay_filters_and_export():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    js = (STATIC / "app.js").read_text(encoding="utf-8")

    for control_id in [
        "replay-source-filter",
        "replay-event-type-filter",
        "replay-export-button",
    ]:
        assert f'id="{control_id}"' in html

    assert "function replayQueryParams" in js
    assert "function exportReplayMarkdown" in js
    assert '$("#replay-export-button")' in js


def test_static_ui_exposes_audit_security_redaction_scan():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    js = (STATIC / "app.js").read_text(encoding="utf-8")

    audit_section = html.split('id="view-audit-security"', 1)[1].split("</section>", 1)[0]
    for control_id in [
        "audit-security-redaction-text",
        "audit-security-redaction-button",
        "audit-security-redaction-output",
    ]:
        assert f'id="{control_id}"' in audit_section

    assert 'id="audit-security-redaction-button" class="primary" data-label="Scan"' in audit_section
    assert "async function runAuditSecurityRedactionScan" in js
    assert "/api/init/redaction-scan" in js
    assert '$("#audit-security-redaction-button")' in js
    assert "renderActionError(output, err" in js


def test_static_ui_exposes_browser_session_lab_controls():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    js = (STATIC / "app.js").read_text(encoding="utf-8")

    assert 'data-view="browser-lab"' in html
    assert 'id="view-browser-lab"' in html
    for control_id in [
        "browser-lab-source",
        "browser-lab-status-button",
        "browser-lab-open-login-button",
        "browser-lab-har-path",
        "browser-lab-hosts",
        "browser-lab-configure-external-apply",
        "browser-lab-import-har-button",
        "browser-lab-persona-json",
        "browser-lab-form-json",
        "browser-lab-map-form-button",
        "browser-lab-dry-run-button",
        "browser-lab-execute-dry-run-button",
        "browser-lab-output",
    ]:
        assert f'id="{control_id}"' in html

    assert "async function loadBrowserLabStatus" in js
    assert "async function openBrowserLabLogin" in js
    assert "async function importBrowserLabHar" in js
    assert "configure_external_apply" in js
    assert '$("#browser-lab-configure-external-apply")' in js
    assert "function browserLabRequestContext" in js
    assert "async function mapBrowserLabForm" in js
    assert "async function dryRunBrowserLabForm" in js
    assert "async function executeBrowserLabDryRun" in js
    assert "/api/browser-lab/status" in js
    assert "/api/browser-lab/import-har" in js
    assert "/api/browser-lab/forms/map" in js
    assert "/api/browser-lab/forms/execute-dry-run" in js
    assert "persona: context.persona" in js
    assert '$("#browser-lab-import-har-button")' in js
    assert '$("#browser-lab-map-form-button")' in js


def test_static_ui_exposes_campaign_safety_controls():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    js = (STATIC / "app.js").read_text(encoding="utf-8")

    for control_id in [
        "campaign-daily-cap",
        "campaign-pause-state",
        "campaign-confirm-run-button",
        "campaign-kill-switch-button",
        "campaign-resume-button",
    ]:
        assert f'id="{control_id}"' in html

    assert "async function confirmCampaignRun" in js
    assert "async function killCampaigns" in js
    assert "async function resumeCampaigns" in js
    assert "daily_cap" in js
    assert "/api/agent/preflight" in js
    assert "/api/agent/pause" in js
    assert "/api/agent/resume" in js
    assert '$("#campaign-kill-switch-button")' in js
    assert '$("#campaign-resume-button")' in js


def test_static_ui_exposes_external_campaign_controls():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    js = (STATIC / "app.js").read_text(encoding="utf-8")

    for control_id in [
        "external-campaign-source",
        "external-campaign-plan-button",
        "external-campaign-run-button",
    ]:
        assert f'id="{control_id}"' in html

    assert "async function planExternalCampaign" in js
    assert "async function confirmExternalCampaignRun" in js
    assert "/api/campaigns/external/plan" in js
    assert "/run-external" in js
    assert '$("#external-campaign-run-button")' in js


def test_static_ui_exposes_external_apply_executor_controls():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    js = (STATIC / "app.js").read_text(encoding="utf-8")

    for control_id in [
        "external-apply-form-json",
        "external-apply-submit-certified",
        "external-apply-dry-run-button",
        "external-apply-confirm-button",
    ]:
        assert f'id="{control_id}"' in html

    assert "async function dryRunExternalApply" in js
    assert "async function confirmExternalApply" in js
    assert "submit_certified" in js
    assert "/external-apply/dry-run" in js
    assert "/external-apply/confirm" in js


def test_static_ui_exposes_pipeline_and_interview_prep_controls():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    js = (STATIC / "app.js").read_text(encoding="utf-8")

    for control_id in [
        "pipeline-job-id",
        "pipeline-stage-select",
        "pipeline-event-at",
        "pipeline-refresh-button",
        "pipeline-prep-pack-button",
        "pipeline-schedule-followup-button",
        "pipeline-status-output",
        "pipeline-prep-output",
    ]:
        assert f'id="{control_id}"' in html

    assert "async function loadPipelineStatus" in js
    assert "async function buildPipelinePrepPack" in js
    assert "async function schedulePipelineFollowup" in js
    assert "/api/pipeline/jobs/" in js


def test_static_ui_exposes_human_cover_letter_preview_controls():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    js = (STATIC / "app.js").read_text(encoding="utf-8")

    for control_id in [
        "letter-template-select",
        "letter-preview-button",
        "letter-use-campaign-button",
        "letter-preview-output",
    ]:
        assert f'id="{control_id}"' in html

    assert "async function previewHumanLetter" in js
    assert "use_for_campaign" in js
    assert "/letter-preview" in js


def test_static_ui_exposes_russian_ux_copy_and_button_hints():
    js = (STATIC / "app.js").read_text(encoding="utf-8")

    assert "const UX_RU_COPY" in js
    assert "const CONTROL_HINTS" in js
    assert "function applyRussianUxCopy" in js
    assert "function enhanceButtonHints" in js

    for text in [
        "Настройка",
        "Карта кандидата",
        "Черновик отклика",
        "Кампании",
        "Подготовка к собеседованию",
        "Лаборатория браузера",
        "Синхронизировать вакансии из всех активных источников",
        "Проверить и пересчитать релевантность вакансий",
    ]:
        assert text in js

    assert 'setAttribute("title"' in js
    assert 'setAttribute("aria-label"' in js
    assert 'setAttribute("aria-current", "page")' in js


def test_static_ui_exposes_onboarding_guidance_surface():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    js = (STATIC / "app.js").read_text(encoding="utf-8")

    for control_id in [
        "ux-help-strip",
        "onboarding-guide",
        "onboarding-progress",
        "onboarding-guide-steps",
        "dismiss-onboarding-guide",
    ]:
        assert f'id="{control_id}"' in html

    assert "function renderOnboardingGuide" in js
    assert "function dismissOnboardingGuide" in js
    assert "work-hunter-onboarding-dismissed" in js
    assert "Ответь на вопросы профиля" in js
    assert "Синхронизируй источники" in js
    assert "Выбери вакансию" in js


def test_static_css_supports_help_states_and_accessible_focus():
    css = (STATIC / "app.css").read_text(encoding="utf-8")

    for selector in [
        ".ux-help-strip",
        ".onboarding-guide",
        ".onboarding-progress",
        ".onboarding-step",
        ".field-hint",
        "button:focus-visible",
        "input:focus-visible",
    ]:
        assert selector in css


def test_service_worker_cache_version_tracks_ux_asset_refresh():
    sw = (STATIC / "sw.js").read_text(encoding="utf-8")

    assert 'CACHE_NAME = "work-hunter-v8"' in sw


def test_static_ui_exposes_source_setup_wizard():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    css = (STATIC / "app.css").read_text(encoding="utf-8")

    for control_id in [
        "sidebar-source-setup-button",
        "open-source-setup-button",
        "source-setup-modal",
        "source-setup-source",
        "source-setup-level",
        "source-setup-status",
        "source-setup-steps",
        "source-setup-next-action",
        "source-setup-log",
        "source-setup-har-path",
        "source-setup-har-hosts",
        "source-setup-redaction-payload",
        "source-setup-form-json",
    ]:
        assert f'id="{control_id}"' in html

    for marker in [
        "Мастер подключения источника",
        "function openSourceSetupWizard",
        "function loadSourceSetupGuide",
        "async function runSourceSetupAction",
        "/api/source-setup/guide",
        "/api/source-setup/action",
        "certifiable_external",
        "manual_or_search_only",
    ]:
        assert marker in js

    for selector in [
        ".source-setup-grid",
        ".source-setup-step",
        ".source-setup-next",
        ".source-setup-log",
        ".source-setup-status-main",
        ".source-setup-log-shell",
    ]:
        assert selector in css


def test_static_ui_source_setup_defaults_to_simple_hh_path():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    js = (STATIC / "app.js").read_text(encoding="utf-8")

    assert '<option value="hh" selected>HH.ru (рекомендуется)</option>' in html
    assert '<details class="advanced-block source-setup-inputs">' in html
    assert "Технические настройки для внешних площадок" in html
    assert "Журнал проверок" in html
    assert "Самый простой путь" in js
    assert "Проверить вход в HH" in js
    assert "Подключить HAR-файл" in js
    assert "Расширенные проверки" in js
    assert "function sourceSetupCompactLogLine" in js
    assert "function sourceSetupNeedsTechnicalInputs" in js
    assert "resetSourceSetupGuide" in js
    assert "const primaryLabel = SOURCE_SETUP_ACTION_LABELS[primaryAction] || action.label" in js
    assert "runNext.innerHTML = `<i data-lucide=\"play\"></i>${escapeHtml(primaryLabel)}`;" in js
    assert 'if (id === "source-setup-run-next") continue;' in js
