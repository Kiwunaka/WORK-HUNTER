from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .api_discovery import discover_api_candidates_from_url
from .api_probe import probe_api_candidates_from_url
from .api_recon import analyze_har
from .config import active_profile, mask_secrets
from .external_adapter_plan import build_external_adapter_plan
from .external_sessions import (
    call_external_session,
    import_external_session_from_har,
    list_external_sessions,
    show_external_session,
)
from .scheduler import SafeTaskRunner, load_task_plan
from .services import WorkHunter
from .sources import PUBLIC_BOARD_SOURCE_NAMES


SYNC_SOURCE_CHOICES = ["all", "hh", "habr", "geekjob", "telegram", *PUBLIC_BOARD_SOURCE_NAMES]
LIST_SOURCE_CHOICES = ["hh", "habr", "geekjob", "telegram", *PUBLIC_BOARD_SOURCE_NAMES]


def main(argv: list[str] | None = None) -> None:
    _configure_output()
    parser = argparse.ArgumentParser(prog="work-hunter")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    sub = parser.add_subparsers(dest="command", required=True)

    init_cmd = sub.add_parser("init")
    init_cmd.add_argument("--check", action="store_true")
    init_cmd.add_argument("--json", action="store_true")
    init_cmd.add_argument("--refresh-docs", action="store_true")
    init_cmd.add_argument("--with-ai", action="store_true")
    init_cmd.add_argument("--with-browser", action="store_true")
    init_cmd.add_argument("--import-wo")
    init_cmd.add_argument("--onboard", action="store_true")
    init_cmd.add_argument("--dry-run", action="store_true")
    init_cmd.add_argument("--force", action="store_true")

    sync = sub.add_parser("sync")
    sync.add_argument("--source", action="append", choices=SYNC_SOURCE_CHOICES)
    sync.add_argument("--limit", type=int)

    score = sub.add_parser("score")
    score.add_argument("--limit", type=int, default=10000)

    list_cmd = sub.add_parser("list")
    list_cmd.add_argument("--limit", type=int, default=20)
    list_cmd.add_argument("--source", choices=LIST_SOURCE_CHOICES)
    list_cmd.add_argument("--status")
    list_cmd.add_argument("--min-score", type=int)
    list_cmd.add_argument("--json", action="store_true")

    letter = sub.add_parser("letter")
    letter.add_argument("job_id", type=int)

    status = sub.add_parser("status")
    status.add_argument("job_id", type=int)
    status.add_argument("status")
    status.add_argument("--note", default="")

    apply_hh = sub.add_parser("apply-hh")
    apply_hh.add_argument("job_id", type=int)
    apply_hh.add_argument("--resume-id")
    apply_hh.add_argument("--real", action="store_true")

    apply_plan = sub.add_parser("apply-plan")
    apply_plan.add_argument("job_id", type=int)
    apply_plan.add_argument("--resume-id")
    apply_plan.add_argument("--letter", default="")
    apply_plan.add_argument("--letter-file", type=Path)

    hh_apply_from_file = sub.add_parser("hh-apply-from-file")
    hh_apply_from_file.add_argument("path", type=Path)
    hh_apply_from_file.add_argument("--resume-id")
    hh_apply_from_file.add_argument("--template", default="")
    hh_apply_from_file.add_argument("--template-file", type=Path)
    hh_apply_from_file.add_argument("--limit", type=int)
    hh_apply_from_file.add_argument("--min-interval", type=float, default=0.0)
    hh_apply_from_file.add_argument("--real", action="store_true")
    hh_apply_from_file.add_argument("--confirm", action="store_true")
    hh_apply_from_file.add_argument("--include-processed", action="store_true")

    sub.add_parser("hh-whoami")
    sub.add_parser("hh-summary")
    sub.add_parser("hh-auth-status")
    sub.add_parser("hh-refresh-token")

    hh_account = sub.add_parser("hh-account")
    hh_account_sub = hh_account.add_subparsers(dest="account_command", required=True)
    hh_account_sub.add_parser("list")
    hh_account_use = hh_account_sub.add_parser("use")
    hh_account_use.add_argument("name")
    hh_account_set = hh_account_sub.add_parser("set-token")
    hh_account_set.add_argument("name")
    hh_account_set.add_argument("--access-token")
    hh_account_set.add_argument("--refresh-token")
    hh_account_set.add_argument("--access-expires-at")
    hh_account_set.add_argument("--client-id")
    hh_account_set.add_argument("--client-secret")

    runner = sub.add_parser("runner")
    runner.add_argument("--plan", type=Path, required=True)

    doctor = sub.add_parser("doctor")
    doctor.add_argument("--json", action="store_true")

    hh_resumes = sub.add_parser("hh-resumes")
    hh_resumes.add_argument("--sync", action="store_true")
    hh_resumes.add_argument("--json", action="store_true")

    sub.add_parser("hh-update-resumes")

    hh_create_resume = sub.add_parser("hh-create-resume")
    hh_create_resume.add_argument("--payload")
    hh_create_resume.add_argument("--payload-file", type=Path)
    hh_create_resume.add_argument("--dry-run", action="store_true")
    hh_create_resume.add_argument("--publish", action="store_true")
    hh_create_resume.add_argument("--validate", action="store_true")

    hh_resume_template_preview = sub.add_parser("hh-resume-template-preview")
    hh_resume_template_preview.add_argument("--template")
    hh_resume_template_preview.add_argument("--template-file", type=Path)
    hh_resume_template_preview.add_argument("--context", default="{}")

    hh_batch_matrix = sub.add_parser("hh-batch-matrix")
    hh_batch_matrix.add_argument("--matrix")
    hh_batch_matrix.add_argument("--matrix-file", type=Path)

    hh_clone_resume = sub.add_parser("hh-clone-resume")
    hh_clone_resume.add_argument("resume_id")
    hh_clone_resume.add_argument("--title")
    hh_clone_resume.add_argument("--dry-run", action="store_true", default=True)
    hh_clone_resume.add_argument("--real", action="store_true")
    hh_clone_resume.add_argument("--publish", action="store_true")

    hh_negotiations = sub.add_parser("hh-negotiations")
    hh_negotiations.add_argument("--sync", action="store_true")
    hh_negotiations.add_argument("--status", default="active")

    hh_negotiation_cleanup = sub.add_parser("hh-negotiation-cleanup")
    hh_negotiation_cleanup.add_argument("--status", default="active")
    hh_negotiation_cleanup.add_argument("--max-age-days", type=int)
    hh_negotiation_cleanup.add_argument("--decline-message", default="")
    hh_negotiation_cleanup.add_argument("--blacklist", action="store_true")
    hh_negotiation_cleanup.add_argument("--confirm", action="store_true")
    hh_negotiation_cleanup.add_argument("--now")

    hh_reply_employers = sub.add_parser("hh-reply-employers")
    hh_reply_employers.add_argument("--template", required=True)
    hh_reply_employers.add_argument("--status", default="active")
    hh_reply_employers.add_argument("--limit", type=int)
    hh_reply_employers.add_argument("--confirm", action="store_true")

    hh_skipped = sub.add_parser("hh-skipped")
    hh_skipped.add_argument("--clear", action="store_true")

    hh_call_api = sub.add_parser("hh-call-api")
    hh_call_api.add_argument("method")
    hh_call_api.add_argument("path")
    hh_call_api.add_argument("--data")

    hh_preset = sub.add_parser("hh-preset")
    hh_preset_sub = hh_preset.add_subparsers(dest="preset_command", required=True)
    hh_preset_sub.add_parser("list")
    hh_preset_get = hh_preset_sub.add_parser("get")
    hh_preset_get.add_argument("name")
    hh_preset_save = hh_preset_sub.add_parser("save")
    hh_preset_save.add_argument("name")
    hh_preset_save.add_argument("--params", required=True)
    hh_preset_delete = hh_preset_sub.add_parser("delete")
    hh_preset_delete.add_argument("name")

    hh_campaign_plan = sub.add_parser("hh-campaign-plan")
    hh_campaign_plan.add_argument("--limit", type=int, default=100)
    hh_campaign_plan.add_argument("--min-score", type=int, default=0)
    hh_campaign_plan.add_argument("--skip-tests", action="store_true")
    hh_campaign_plan.add_argument("--ai-filter-mode", choices=["off", "light", "heavy"], default="off")
    hh_campaign_plan.add_argument("--resume-id")
    hh_campaign_plan.add_argument("--daily-cap", type=int)

    hh_search_campaign_plan = sub.add_parser("hh-search-campaign-plan")
    hh_search_campaign_plan.add_argument("--text")
    hh_search_campaign_plan.add_argument("--area", action="append")
    hh_search_campaign_plan.add_argument("--professional-role", action="append")
    hh_search_campaign_plan.add_argument("--industry", action="append")
    hh_search_campaign_plan.add_argument("--salary", type=int)
    hh_search_campaign_plan.add_argument("--schedule")
    hh_search_campaign_plan.add_argument("--experience")
    hh_search_campaign_plan.add_argument("--employment", action="append")
    hh_search_campaign_plan.add_argument("--date-from")
    hh_search_campaign_plan.add_argument("--date-to")
    hh_search_campaign_plan.add_argument("--search-field", action="append")
    hh_search_campaign_plan.add_argument("--employer-id", action="append")
    hh_search_campaign_plan.add_argument("--excluded-employer-id", action="append")
    hh_search_campaign_plan.add_argument("--only-with-salary", action="store_true")
    hh_search_campaign_plan.add_argument("--limit", type=int, default=100)
    hh_search_campaign_plan.add_argument("--page", type=int, default=0)
    hh_search_campaign_plan.add_argument("--min-score", type=int, default=0)
    hh_search_campaign_plan.add_argument("--skip-tests", action="store_true")
    hh_search_campaign_plan.add_argument("--ai-filter-mode", choices=["off", "light", "heavy"], default="off")
    hh_search_campaign_plan.add_argument("--resume-id")
    hh_search_campaign_plan.add_argument("--daily-cap", type=int)

    hh_campaign_confirm = sub.add_parser("hh-campaign-confirm")
    hh_campaign_confirm.add_argument("run_id", type=int)
    hh_campaign_confirm.add_argument("--confirm", action="store_true")

    report = sub.add_parser("report")
    report.add_argument("--limit", type=int, default=10)

    query = sub.add_parser("query")
    query.add_argument("sql")

    config = sub.add_parser("config")
    config.add_argument("--json", action="store_true")

    ai = sub.add_parser("ai")
    ai_sub = ai.add_subparsers(dest="ai_command", required=True)
    ai_sub.add_parser("status")
    ai_test = ai_sub.add_parser("test")
    ai_test.add_argument("--route")
    ai_test.add_argument("--prompt", default="ping")
    ai_test.add_argument("--dry-run", action="store_true")

    browser = sub.add_parser("browser")
    browser_sub = browser.add_subparsers(dest="browser_command", required=True)
    browser_login = browser_sub.add_parser("login")
    browser_login.add_argument("source", choices=LIST_SOURCE_CHOICES)
    browser_login.add_argument("--login-url", default="")
    browser_status = browser_sub.add_parser("status")
    browser_status.add_argument("source", nargs="?", default="getmatch", choices=LIST_SOURCE_CHOICES)

    source = sub.add_parser("source")
    source_sub = source.add_subparsers(dest="source_command", required=True)
    source_status = source_sub.add_parser("status")
    source_status.add_argument("source", nargs="?", choices=LIST_SOURCE_CHOICES)
    source_certification_matrix = source_sub.add_parser("certification-matrix")
    source_certification_matrix.add_argument("--level", type=int, default=5)
    source_certification_matrix.add_argument("--source", action="append", choices=LIST_SOURCE_CHOICES)
    source_certification_plan = source_sub.add_parser("certification-plan")
    source_certification_plan.add_argument("--level", type=int, default=5)
    source_certification_plan.add_argument("--source", action="append", choices=LIST_SOURCE_CHOICES)
    source_certification_audit = source_sub.add_parser("certification-audit")
    source_certification_audit.add_argument("source", choices=LIST_SOURCE_CHOICES)
    source_certification_audit.add_argument("--level", type=int, default=5)
    source_certification_audit.add_argument("--evidence", default="{}")
    source_certification_evidence = source_sub.add_parser("certification-evidence")
    source_certification_evidence.add_argument("source", choices=LIST_SOURCE_CHOICES)
    source_certification_evidence.add_argument("--level", type=int, default=5)
    source_certification_evidence.add_argument("--evidence", default="{}")
    source_external_apply_target = source_sub.add_parser("external-apply-target")
    source_external_apply_target.add_argument("source", choices=LIST_SOURCE_CHOICES)
    source_external_apply_target.add_argument("--session", required=True)
    source_external_apply_target.add_argument("--url", required=True)
    source_external_apply_target.add_argument("--method", default="POST")
    source_external_apply_target.add_argument("--payload-template", default="{}")
    source_external_apply_target.add_argument("--level", type=int, default=5)
    source_external_apply_from_har = source_sub.add_parser("external-apply-from-har")
    source_external_apply_from_har.add_argument("source", choices=LIST_SOURCE_CHOICES)
    source_external_apply_from_har.add_argument("path", type=Path)
    source_external_apply_from_har.add_argument("--host", action="append", default=[])
    source_external_apply_from_har.add_argument("--level", type=int, default=5)
    source_redaction_scan = source_sub.add_parser("redaction-scan")
    source_redaction_scan.add_argument("source", choices=LIST_SOURCE_CHOICES)
    source_redaction_scan.add_argument("--payload", default="{}")
    source_redaction_scan.add_argument("--text", default="")
    source_redaction_scan.add_argument("--level", type=int, default=5)
    source_certify = source_sub.add_parser("certify")
    source_certify.add_argument("source", choices=LIST_SOURCE_CHOICES)
    source_certify.add_argument("--level", type=int, default=5)
    source_certify.add_argument("--evidence", default="{}")
    source_sync = source_sub.add_parser("sync")
    source_sync.add_argument("source", choices=SYNC_SOURCE_CHOICES)
    source_sync.add_argument("--limit", type=int)
    source_prepare_apply = source_sub.add_parser("prepare-apply")
    source_prepare_apply.add_argument("job_id", type=int)
    source_prepare_apply.add_argument("--resume-id")
    source_prepare_apply.add_argument("--letter", default="")
    source_prepare_apply.add_argument("--letter-file", type=Path)
    source_dry_run_apply = source_sub.add_parser("dry-run-apply")
    source_dry_run_apply.add_argument("job_id", type=int)
    source_dry_run_apply.add_argument("--form", default="{}")
    source_dry_run_apply.add_argument("--form-file", type=Path)
    source_dry_run_apply.add_argument("--resume-variant", default="{}")
    source_dry_run_apply.add_argument("--resume-variant-file", type=Path)
    source_dry_run_apply.add_argument("--cover-letter", default="")
    source_dry_run_apply.add_argument("--cover-letter-file", type=Path)
    source_dry_run_apply.add_argument("--short-message", default="")
    source_dry_run_apply.add_argument("--campaign-policy", default="{}")
    source_dry_run_apply.add_argument("--extra-answers", default="{}")

    sub.add_parser("onboard")

    profile = sub.add_parser("profile")
    profile_sub = profile.add_subparsers(dest="profile_command", required=True)
    profile_sub.add_parser("show")

    resume = sub.add_parser("resume")
    resume_sub = resume.add_subparsers(dest="resume_command", required=True)
    resume_import = resume_sub.add_parser("import")
    resume_import.add_argument("path", type=Path)
    resume_import.add_argument("--activate", action="store_true")

    campaign = sub.add_parser("campaign")
    campaign_sub = campaign.add_subparsers(dest="campaign_command", required=True)
    campaign_preset = campaign_sub.add_parser("preset")
    campaign_preset_sub = campaign_preset.add_subparsers(dest="campaign_preset_command", required=True)
    campaign_preset_create = campaign_preset_sub.add_parser("create")
    campaign_preset_create.add_argument("name")
    campaign_preset_create.add_argument("--params", default="{}")
    campaign_preset_sub.add_parser("list")
    campaign_preset_get = campaign_preset_sub.add_parser("get")
    campaign_preset_get.add_argument("name")
    campaign_preset_delete = campaign_preset_sub.add_parser("delete")
    campaign_preset_delete.add_argument("name")
    campaign_plan = campaign_sub.add_parser("plan")
    campaign_plan.add_argument("preset", nargs="?")
    campaign_plan.add_argument("--dry-run", action="store_true")
    campaign_plan.add_argument("--limit", type=int)
    campaign_plan.add_argument("--min-score", type=int)
    campaign_plan.add_argument("--skip-tests", action="store_true", default=None)
    campaign_plan.add_argument("--ai-filter-mode", choices=["off", "light", "heavy"])
    campaign_plan.add_argument("--resume-id")
    campaign_plan.add_argument("--daily-cap", type=int)
    campaign_run = campaign_sub.add_parser("run")
    campaign_run.add_argument("target")
    campaign_run.add_argument("--real", action="store_true")
    campaign_pause = campaign_sub.add_parser("pause")
    campaign_pause.add_argument("target", nargs="?")
    campaign_pause.add_argument("--reason")
    campaign_review = campaign_sub.add_parser("review")
    campaign_review.add_argument("target", nargs="?", default="latest")
    campaign_enable = campaign_sub.add_parser("enable")
    campaign_enable.add_argument("target", nargs="?", default="latest")
    campaign_resume = campaign_sub.add_parser("resume")
    campaign_resume.add_argument("target", nargs="?", default="latest")
    campaign_resume.add_argument("--reason")
    campaign_kill = campaign_sub.add_parser("kill")
    campaign_kill.add_argument("target", nargs="?", default="latest")
    campaign_kill.add_argument("--reason")

    replay = sub.add_parser("replay")
    replay_sub = replay.add_subparsers(dest="replay_command", required=True)
    replay_campaign = replay_sub.add_parser("campaign")
    replay_campaign.add_argument("target", nargs="?", default="latest")
    replay_campaign.add_argument("--markdown", action="store_true")
    replay_campaign.add_argument("--source")
    replay_campaign.add_argument("--event-type")
    replay_job = replay_sub.add_parser("job")
    replay_job.add_argument("job_id", type=int)
    replay_job.add_argument("--markdown", action="store_true")
    replay_job.add_argument("--source")
    replay_job.add_argument("--event-type")

    sub.add_parser("source-capabilities")

    ui = sub.add_parser("ui")
    ui.add_argument("--host")
    ui.add_argument("--port", type=int)

    sub.add_parser("mcp")

    api_recon_har = sub.add_parser("api-recon-har")
    api_recon_har.add_argument("path", type=Path)
    api_recon_har.add_argument("--host", action="append", default=[])

    api_discover_url = sub.add_parser("api-discover-url")
    api_discover_url.add_argument("url")
    api_discover_url.add_argument("--host", action="append", default=[])
    api_discover_url.add_argument("--max-scripts", type=int, default=12)

    api_probe_url = sub.add_parser("api-probe-url")
    api_probe_url.add_argument("url")
    api_probe_url.add_argument("--host", action="append", default=[])
    api_probe_url.add_argument("--max-scripts", type=int, default=12)
    api_probe_url.add_argument("--limit", type=int, default=25)
    api_probe_url.add_argument("--timeout", type=int, default=10)

    external_adapter_plan = sub.add_parser("external-adapter-plan")
    external_adapter_plan.add_argument("path", type=Path)
    external_adapter_plan.add_argument("--source", required=True)
    external_adapter_plan.add_argument("--host", action="append", default=[])

    external_session = sub.add_parser("external-session")
    external_session_sub = external_session.add_subparsers(dest="external_session_command", required=True)
    external_session_import = external_session_sub.add_parser("import-har")
    external_session_import.add_argument("name")
    external_session_import.add_argument("path", type=Path)
    external_session_import.add_argument("--host", action="append", required=True)
    external_session_sub.add_parser("list")
    external_session_show = external_session_sub.add_parser("show")
    external_session_show.add_argument("name")
    external_session_call = external_session_sub.add_parser("call")
    external_session_call.add_argument("name")
    external_session_call.add_argument("method")
    external_session_call.add_argument("url")
    external_session_call.add_argument("--data")
    external_session_call.add_argument("--data-file", type=Path)
    external_session_call.add_argument("--real", action="store_true")
    external_session_call.add_argument("--unsafe-lab", action="store_true")
    external_session_call.add_argument("--timeout", type=int, default=20)

    args = parser.parse_args(argv)
    app = WorkHunter(args.root)

    if args.command == "init":
        if (
            args.check
            or args.json
            or args.refresh_docs
            or args.with_ai
            or args.with_browser
            or args.import_wo
            or args.onboard
            or args.dry_run
            or args.force
        ):
            report = app.init_report(
                check=args.check,
                refresh_docs=args.refresh_docs,
                with_ai=args.with_ai,
                with_browser=args.with_browser,
                import_wo=args.import_wo,
                dry_run=args.dry_run,
                force=args.force,
            )
            if args.onboard:
                report["onboarding"] = _onboarding_summary(app)
            print_json(report) if args.json else print(f"Config: {report['config_path']}")
        else:
            path = app.init()
            print(f"Config: {path}")
        return
    if args.command == "sync":
        sources = None if args.source is None or "all" in args.source else args.source
        print_json(app.sync_sources(sources=sources, limit=args.limit))
        return
    if args.command == "score":
        print(f"Scored jobs: {app.score_jobs(limit=args.limit)}")
        return
    if args.command == "list":
        jobs = app.list_jobs(
            limit=args.limit,
            source=args.source,
            status=args.status,
            min_score=args.min_score,
        )
        if args.json:
            print_json([job.to_dict() for job in jobs])
        else:
            for job in jobs:
                score = job.score.total_score if job.score else "-"
                print(f"#{job.id} [{score}] {job.source} {job.title} @ {job.company} - {job.url}")
        return
    if args.command == "letter":
        print(app.prepare_letter(args.job_id).body)
        return
    if args.command == "status":
        app.mark_job(args.job_id, args.status, args.note)
        print("ok")
        return
    if args.command == "apply-hh":
        print_json(
            app.apply_hh(
                args.job_id,
                resume_id=args.resume_id,
                dry_run=not args.real,
            )
        )
        return
    if args.command == "apply-plan":
        letter = args.letter or None
        if args.letter_file:
            letter = args.letter_file.read_text(encoding="utf-8")
        print_json(app.prepare_apply_plan(args.job_id, resume_id=args.resume_id, letter=letter))
        return
    if args.command == "hh-apply-from-file":
        template = args.template
        if args.template_file:
            template = args.template_file.read_text(encoding="utf-8")
        print_json(
            app.apply_hh_from_file(
                args.path,
                resume_id=args.resume_id,
                template=template,
                dry_run=not args.real,
                confirm=args.confirm,
                limit=args.limit,
                min_interval_seconds=args.min_interval,
                skip_processed=not args.include_processed,
            )
        )
        return
    if args.command == "hh-whoami":
        print_json(app.hh_whoami())
        return
    if args.command == "hh-summary":
        print_json(app.hh_operator_summary())
        return
    if args.command == "hh-auth-status":
        print_json(app.hh_auth_status())
        return
    if args.command == "hh-refresh-token":
        print_json(mask_secrets(app.refresh_hh_token()))
        return
    if args.command == "hh-account":
        if args.account_command == "list":
            print_json(app.list_hh_account_profiles())
        elif args.account_command == "use":
            print_json(app.use_hh_account_profile(args.name))
        elif args.account_command == "set-token":
            print_json(
                app.save_hh_account_profile(
                    args.name,
                    access_token=args.access_token,
                    refresh_token=args.refresh_token,
                    access_expires_at=args.access_expires_at,
                    client_id=args.client_id,
                    client_secret=args.client_secret,
                )
            )
        return
    if args.command == "runner":
        print_json(SafeTaskRunner(app, root=args.root).run(load_task_plan(args.plan)))
        return
    if args.command == "doctor":
        report = app.doctor_report()
        print_json(report) if args.json else print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    if args.command == "hh-resumes":
        if args.sync:
            print_json(app.sync_hh_resumes())
        else:
            resumes = [resume.to_dict() for resume in app.storage.list_hh_resumes()]
            print_json(resumes)
        return
    if args.command == "hh-update-resumes":
        print_json(app.update_hh_resumes())
        return
    if args.command == "hh-create-resume":
        if args.payload_file:
            print_json(
                app.create_hh_resume_from_file(
                    args.payload_file,
                    dry_run=args.dry_run,
                    publish=args.publish,
                    validate=args.validate,
                )
            )
            return
        if not args.payload:
            parser.error("hh-create-resume requires --payload or --payload-file")
        print_json(
            app.create_hh_resume(
                json.loads(args.payload),
                dry_run=args.dry_run,
                publish=args.publish,
                validate=args.validate,
            )
        )
        return
    if args.command == "hh-resume-template-preview":
        template = args.template or ""
        if args.template_file:
            template = args.template_file.read_text(encoding="utf-8")
        if not template:
            parser.error("hh-resume-template-preview requires --template or --template-file")
        print_json(app.preview_hh_resume_template(template, context=json.loads(args.context or "{}")))
        return
    if args.command == "hh-batch-matrix":
        if args.matrix_file:
            spec = json.loads(args.matrix_file.read_text(encoding="utf-8"))
        elif args.matrix:
            spec = json.loads(args.matrix)
        else:
            parser.error("hh-batch-matrix requires --matrix or --matrix-file")
        print_json(app.build_hh_batch_preset_matrix(spec))
        return
    if args.command == "hh-clone-resume":
        print_json(
            app.clone_hh_resume(
                args.resume_id,
                title=args.title,
                dry_run=not args.real,
                publish=args.publish,
            )
        )
        return
    if args.command == "hh-negotiations":
        if args.sync:
            print_json(app.sync_hh_negotiations(status=args.status))
        else:
            print_json([item.to_dict() for item in app.storage.list_hh_negotiations()])
        return
    if args.command == "hh-negotiation-cleanup":
        if args.confirm:
            print_json(
                app.confirm_hh_negotiation_cleanup(
                    status=args.status,
                    max_age_days=args.max_age_days,
                    blacklist=args.blacklist,
                    decline_message=args.decline_message,
                    now=args.now,
                    confirm=True,
                )
            )
        else:
            print_json(
                app.plan_hh_negotiation_cleanup(
                    status=args.status,
                    max_age_days=args.max_age_days,
                    now=args.now,
                )
            )
        return
    if args.command == "hh-reply-employers":
        print_json(
            app.reply_hh_employers(
                template=args.template,
                status=args.status,
                limit=args.limit,
                dry_run=not args.confirm,
                confirm=args.confirm,
            )
        )
        return
    if args.command == "hh-skipped":
        if args.clear:
            print_json(app.clear_hh_skipped_vacancies())
        else:
            print_json([item.to_dict() for item in app.storage.list_hh_skipped_vacancies()])
        return
    if args.command == "hh-call-api":
        data = json.loads(args.data) if args.data else None
        print_json(app.hh_call_api(args.method, args.path, data=data))
        return
    if args.command == "hh-preset":
        if args.preset_command == "list":
            print_json(app.list_hh_campaign_presets())
        elif args.preset_command == "get":
            print_json(app.get_hh_campaign_preset(args.name))
        elif args.preset_command == "save":
            print_json(app.save_hh_campaign_preset(args.name, json.loads(args.params)))
        elif args.preset_command == "delete":
            print_json(app.delete_hh_campaign_preset(args.name))
        return
    if args.command == "hh-campaign-plan":
        print_json(
            app.plan_hh_campaign(
                limit=args.limit,
                min_score=args.min_score,
                skip_tests=args.skip_tests,
                ai_filter_mode=args.ai_filter_mode,
                resume_id=args.resume_id,
                daily_cap=args.daily_cap,
            )
        )
        return
    if args.command == "hh-search-campaign-plan":
        print_json(
            app.plan_hh_search_campaign(
                text=args.text,
                area=args.area,
                professional_role=args.professional_role,
                industry=args.industry,
                salary=args.salary,
                schedule=args.schedule,
                experience=args.experience,
                employment=args.employment,
                date_from=args.date_from,
                date_to=args.date_to,
                search_field=args.search_field,
                employer_id=args.employer_id,
                excluded_employer_id=args.excluded_employer_id,
                only_with_salary=args.only_with_salary,
                limit=args.limit,
                page=args.page,
                min_score=args.min_score,
                skip_tests=args.skip_tests,
                ai_filter_mode=args.ai_filter_mode,
                resume_id=args.resume_id,
                daily_cap=args.daily_cap,
            )
        )
        return
    if args.command == "hh-campaign-confirm":
        print_json(app.confirm_hh_campaign(args.run_id, confirm=args.confirm))
        return
    if args.command == "report":
        print_json(app.daily_report(limit=args.limit))
        return
    if args.command == "query":
        print_json(app.storage.query_readonly(args.sql))
        return
    if args.command == "config":
        data = mask_secrets(app.config)
        print_json(data) if args.json else print(json.dumps(data, ensure_ascii=False, indent=2))
        return
    if args.command == "ai":
        if args.ai_command == "status":
            print_json(app.ai_status())
        elif args.ai_command == "test":
            print_json(app.ai_test(route=args.route, prompt=args.prompt, dry_run=args.dry_run))
        return
    if args.command == "browser":
        if args.browser_command == "login":
            print_json(app.browser_lab_open_login(args.source, login_url=args.login_url))
        elif args.browser_command == "status":
            print_json(app.browser_lab_status(args.source))
        return
    if args.command == "source":
        if args.source_command == "status":
            report = app.source_capabilities()
            print_json({args.source: report.get(args.source)} if args.source else report)
        elif args.source_command == "certification-matrix":
            print_json(app.source_certification_matrix(level=args.level, sources=args.source))
        elif args.source_command == "certification-plan":
            print_json(app.source_certification_plan(level=args.level, sources=args.source))
        elif args.source_command == "certification-audit":
            print_json(
                app.source_certification_audit(
                    args.source,
                    level=args.level,
                    evidence=json.loads(args.evidence or "{}"),
                )
            )
        elif args.source_command == "certification-evidence":
            print_json(
                app.record_source_certification_evidence(
                    args.source,
                    level=args.level,
                    evidence=json.loads(args.evidence or "{}"),
                )
            )
        elif args.source_command == "external-apply-target":
            print_json(
                app.configure_source_external_apply_target(
                    args.source,
                    session=args.session,
                    url=args.url,
                    method=args.method,
                    payload_template=json.loads(args.payload_template or "{}"),
                    level=args.level,
                )
            )
        elif args.source_command == "external-apply-from-har":
            hosts = set(args.host or []) or None
            print_json(
                app.configure_source_external_apply_from_har(
                    args.source,
                    args.path,
                    allowed_hosts=hosts,
                    level=args.level,
                )
            )
        elif args.source_command == "redaction-scan":
            print_json(
                app.record_source_redaction_scan(
                    args.source,
                    payload=json.loads(args.payload or "{}"),
                    text=args.text,
                    level=args.level,
                )
            )
        elif args.source_command == "certify":
            print_json(
                app.promote_source_certification(
                    args.source,
                    level=args.level,
                    evidence=json.loads(args.evidence or "{}"),
                )
            )
        elif args.source_command == "sync":
            sources = None if args.source == "all" else [args.source]
            if args.limit == 0:
                planned_sources = sources or SYNC_SOURCE_CHOICES[1:]
                print_json(
                    {
                        source_name: {"status": "planned", "count": 0, "dry_run": True}
                        for source_name in planned_sources
                    }
                )
            else:
                print_json(app.sync_sources(sources=sources, limit=args.limit))
        elif args.source_command == "prepare-apply":
            print_json(
                {
                    **app.prepare_apply_plan(
                        args.job_id,
                        resume_id=args.resume_id,
                        letter=_cli_text_arg(args.letter, args.letter_file),
                    ),
                    "submit": False,
                }
            )
        elif args.source_command == "dry-run-apply":
            print_json(
                app.external_apply_dry_run(
                    args.job_id,
                    form=_cli_json_object_arg(args.form, args.form_file),
                    resume_variant=_cli_json_object_arg(args.resume_variant, args.resume_variant_file),
                    cover_letter=_cli_text_arg(args.cover_letter, args.cover_letter_file),
                    short_message=args.short_message,
                    campaign_policy=_cli_json_object_arg(args.campaign_policy),
                    extra_answers=_cli_json_object_arg(args.extra_answers),
                )
            )
        return
    if args.command == "onboard":
        print_json(_onboarding_summary(app))
        return
    if args.command == "profile":
        if args.profile_command == "show":
            print_json(_profile_summary(app))
        return
    if args.command == "resume":
        if args.resume_command == "import":
            print_json(app.import_resume(args.path, activate=args.activate))
        return
    if args.command == "campaign":
        if args.campaign_command == "preset":
            if args.campaign_preset_command == "create":
                print_json(app.save_hh_campaign_preset(args.name, json.loads(args.params or "{}")))
            elif args.campaign_preset_command == "list":
                print_json(app.list_hh_campaign_presets())
            elif args.campaign_preset_command == "get":
                print_json(app.get_hh_campaign_preset(args.name))
            elif args.campaign_preset_command == "delete":
                print_json(app.delete_hh_campaign_preset(args.name))
            return
        if args.campaign_command == "plan":
            print_json(app.plan_hh_campaign(**_campaign_plan_kwargs(app, args)))
            return
        if args.campaign_command == "run":
            run_id = _campaign_run_id(app, args.target)
            print_json(app.confirm_enabled_hh_campaign(run_id, confirm=args.real))
            return
        if args.campaign_command == "pause":
            reason = args.reason or f"campaign:{args.target or 'all'}"
            print_json(app.pause_hh_agent(reason=reason))
            return
        if args.campaign_command == "review":
            print_json(_campaign_review_payload(app, _campaign_run_id(app, args.target)))
            return
        if args.campaign_command == "enable":
            run_id = _campaign_run_id(app, args.target)
            app.enable_hh_campaign(run_id)
            print_json(_campaign_review_payload(app, run_id))
            return
        if args.campaign_command == "resume":
            reason = args.reason or f"campaign:{args.target or 'latest'}"
            print_json(app.resume_hh_agent(reason=reason))
            return
        if args.campaign_command == "kill":
            run_id = _campaign_run_id(app, args.target)
            reason = args.reason or f"campaign_kill:{run_id}"
            paused = app.pause_hh_agent(reason=reason)
            run = app.storage.get_hh_campaign_run(run_id)
            if run is not None:
                app.storage.update_hh_campaign_run(run_id, status="killed", counts=run.counts)
            print_json({**paused, "status": "killed", **_campaign_review_payload(app, run_id)})
            return
    if args.command == "replay":
        if args.replay_command == "campaign":
            run_id = _campaign_run_id(app, args.target)
            if args.markdown:
                print(
                    app.export_replay_markdown(
                        run_id=run_id,
                        source=args.source,
                        event_type=args.event_type,
                    ),
                    end="",
                )
            else:
                print_json(app.replay_for_run(run_id, source=args.source, event_type=args.event_type))
        elif args.replay_command == "job":
            if args.markdown:
                print(
                    app.export_replay_markdown(
                        job_id=args.job_id,
                        source=args.source,
                        event_type=args.event_type,
                    ),
                    end="",
                )
            else:
                print_json(app.replay_for_job(args.job_id, source=args.source, event_type=args.event_type))
        return
    if args.command == "source-capabilities":
        print_json(app.source_capabilities())
        return
    if args.command == "ui":
        from .web.server import run_server

        host = args.host or app.config["ui"]["host"]
        port = args.port or int(app.config["ui"]["port"])
        run_server(root=args.root, host=host, port=port)
        return
    if args.command == "mcp":
        from .mcp_server import main as mcp_main

        mcp_main(root=args.root)
        return
    if args.command == "api-recon-har":
        hosts = set(args.host or []) or None
        print_json(analyze_har(args.path, allowed_hosts=hosts))
        return
    if args.command == "api-discover-url":
        hosts = set(args.host or []) or None
        print_json(
            discover_api_candidates_from_url(
                args.url,
                allowed_hosts=hosts,
                max_scripts=args.max_scripts,
            )
        )
        return
    if args.command == "api-probe-url":
        hosts = set(args.host or []) or None
        print_json(
            probe_api_candidates_from_url(
                args.url,
                allowed_hosts=hosts,
                max_scripts=args.max_scripts,
                limit=args.limit,
                timeout=args.timeout,
            )
        )
        return
    if args.command == "external-adapter-plan":
        hosts = set(args.host or []) or None
        print_json(build_external_adapter_plan(args.path, source=args.source, allowed_hosts=hosts))
        return
    if args.command == "external-session":
        if args.external_session_command == "import-har":
            print_json(
                import_external_session_from_har(
                    args.root,
                    args.name,
                    args.path,
                    allowed_hosts=set(args.host or []),
                )
            )
        elif args.external_session_command == "list":
            print_json(list_external_sessions(args.root))
        elif args.external_session_command == "show":
            print_json(show_external_session(args.root, args.name))
        elif args.external_session_command == "call":
            data = args.data
            if args.data_file:
                data = args.data_file.read_text(encoding="utf-8")
            print_json(
                call_external_session(
                    args.root,
                    args.name,
                    args.method,
                    args.url,
                    data=data,
                    real=args.real,
                    unsafe_lab=args.unsafe_lab,
                    timeout=args.timeout,
                )
            )
        return


def print_json(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def _onboarding_summary(app: WorkHunter) -> dict[str, Any]:
    questions = app.onboarding_questions()
    completeness = app.candidate_completeness()
    missing = set(completeness.get("missing") or [])
    next_question = next((question for question in questions if question["id"] in missing), None)
    return {
        "status": completeness["status"],
        "completeness": completeness,
        "next_question": next_question,
        "questions": questions,
    }


def _profile_summary(app: WorkHunter) -> dict[str, Any]:
    return {
        "profile": mask_secrets(active_profile(app.config)),
        "completeness": app.candidate_completeness(),
        "facts": app.candidate_facts(),
    }


def _campaign_plan_kwargs(app: WorkHunter, args: argparse.Namespace) -> dict[str, Any]:
    params: dict[str, Any] = {}
    if args.preset:
        params.update(app.get_hh_campaign_preset(args.preset)["params"])
    for source_name, param_name in [
        ("limit", "limit"),
        ("min_score", "min_score"),
        ("skip_tests", "skip_tests"),
        ("ai_filter_mode", "ai_filter_mode"),
        ("resume_id", "resume_id"),
        ("daily_cap", "daily_cap"),
    ]:
        value = getattr(args, source_name)
        if value is not None:
            params[param_name] = value
    return {
        "limit": int(params.get("limit") or 100),
        "min_score": int(params.get("min_score") or 0),
        "skip_tests": bool(params.get("skip_tests", False)),
        "ai_filter_mode": str(params.get("ai_filter_mode") or "off"),
        "resume_id": params.get("resume_id") or None,
        "daily_cap": params.get("daily_cap"),
    }


def _campaign_run_id(app: WorkHunter, target: str | None) -> int:
    target = str(target or "latest")
    if target.isdigit():
        return int(target)
    runs = app.storage.list_hh_campaign_runs(limit=1)
    if not runs:
        raise ValueError("No campaign runs found")
    return int(runs[0].id)


def _campaign_review_payload(app: WorkHunter, run_id: int) -> dict[str, Any]:
    run = app.storage.get_hh_campaign_run(run_id)
    items = []
    for item in app.storage.list_hh_campaign_items(run_id):
        payload = item.to_dict()
        job = app.storage.get_job(item.job_id)
        payload["job"] = job.to_dict() if job else None
        items.append(payload)
    return {"run": run.to_dict() if run else None, "items": items}


def _cli_json_object_arg(value: str, file_path: Path | None = None) -> dict[str, Any]:
    raw = file_path.read_text(encoding="utf-8") if file_path else str(value or "{}")
    parsed = json.loads(raw or "{}")
    if not isinstance(parsed, dict):
        raise ValueError("Expected a JSON object")
    return parsed


def _cli_text_arg(value: str, file_path: Path | None = None) -> str:
    if file_path is not None:
        return file_path.read_text(encoding="utf-8")
    return str(value or "")


def _configure_output() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
