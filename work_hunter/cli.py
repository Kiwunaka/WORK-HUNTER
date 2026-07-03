from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .api_discovery import discover_api_candidates_from_url
from .api_probe import probe_api_candidates_from_url
from .api_recon import analyze_har
from .config import mask_secrets
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

    sub.add_parser("init")

    doctor = sub.add_parser("doctor")
    doctor.add_argument("--json", action="store_true")

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
    hh_call_api.add_argument("--confirm", action="store_true")

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

    hh_campaign_confirm = sub.add_parser("hh-campaign-confirm")
    hh_campaign_confirm.add_argument("run_id", type=int)
    hh_campaign_confirm.add_argument("--confirm", action="store_true")

    digest = sub.add_parser("digest")
    digest.add_argument("--limit", type=int, default=10)

    report = sub.add_parser("report")
    report.add_argument("--limit", type=int, default=10)

    query = sub.add_parser("query")
    query.add_argument("sql")

    config = sub.add_parser("config")
    config.add_argument("--json", action="store_true")
    config_sub = config.add_subparsers(dest="config_command")
    config_show = config_sub.add_parser("show")
    config_show.add_argument("--json", action="store_true")

    sub.add_parser("source-capabilities")

    source = sub.add_parser("source")
    source_sub = source.add_subparsers(dest="source_command", required=True)
    source_list = source_sub.add_parser("list")
    source_list.add_argument("--json", action="store_true")

    profile = sub.add_parser("profile")
    profile_sub = profile.add_subparsers(dest="profile_command", required=True)
    profile_show = profile_sub.add_parser("show")
    profile_show.add_argument("--json", action="store_true")
    profile_update = profile_sub.add_parser("update")
    profile_update.add_argument("--json")
    profile_update.add_argument("--file", type=Path)

    strategy = sub.add_parser("strategy")
    strategy_sub = strategy.add_subparsers(dest="strategy_command", required=True)
    strategy_sub.add_parser("list")
    strategy_run = strategy_sub.add_parser("run")
    strategy_run.add_argument("name")
    strategy_run.add_argument("--dry-run", action="store_true")
    strategy_run.add_argument("--confirm", action="store_true")
    strategy_run.add_argument("--resume-id")
    strategy_report = strategy_sub.add_parser("report")
    strategy_report.add_argument("name")

    export = sub.add_parser("export")
    export_sub = export.add_subparsers(dest="export_command", required=True)
    export_jobs = export_sub.add_parser("jobs")
    export_jobs.add_argument("--format", choices=["json", "jsonl", "csv"], default="csv")
    export_jobs.add_argument("--source")
    export_jobs.add_argument("--status")
    export_applications = export_sub.add_parser("applications")
    export_applications.add_argument("--format", choices=["json", "jsonl", "csv"], default="jsonl")
    export_report = export_sub.add_parser("report")
    export_report.add_argument("--since", default="")
    export_report.add_argument("--format", choices=["json", "md"], default="json")

    import_cmd = sub.add_parser("import")
    import_sub = import_cmd.add_subparsers(dest="import_command", required=True)
    import_jobs = import_sub.add_parser("jobs")
    import_jobs.add_argument("path", type=Path)

    hh = sub.add_parser("hh")
    hh_sub = hh.add_subparsers(dest="hh_command", required=True)
    hh_sub.add_parser("whoami")
    hh_auth = hh_sub.add_parser("auth")
    hh_auth_sub = hh_auth.add_subparsers(dest="hh_auth_command", required=True)
    hh_auth_sub.add_parser("status")
    hh_auth_sub.add_parser("whoami")
    hh_auth_sub.add_parser("refresh")
    hh_auth_import = hh_auth_sub.add_parser("import-token")
    hh_auth_import.add_argument("--access-token", required=True)
    hh_auth_import.add_argument("--refresh-token", default="")
    hh_auth_import.add_argument("--client-id", default="")
    hh_auth_import.add_argument("--client-secret", default="")
    hh_auth_import.add_argument("--access-expires-at", default="")
    hh_auth_import.add_argument("--profile")
    hh_auth_sub.add_parser("oauth-start")
    hh_auth_sub.add_parser("oauth-callback")

    hh_account_nested = hh_sub.add_parser("account")
    hh_account_nested_sub = hh_account_nested.add_subparsers(dest="hh_account_command", required=True)
    hh_account_nested_sub.add_parser("list")
    hh_account_nested_use = hh_account_nested_sub.add_parser("use")
    hh_account_nested_use.add_argument("name")

    hh_resumes_nested = hh_sub.add_parser("resumes")
    hh_resumes_nested_sub = hh_resumes_nested.add_subparsers(dest="hh_resumes_command", required=True)
    hh_resumes_nested_sub.add_parser("sync")
    hh_resumes_nested_sub.add_parser("list")

    hh_search = hh_sub.add_parser("search")
    hh_search.add_argument("--text")
    hh_search.add_argument("--area", action="append")
    hh_search.add_argument("--professional-role", action="append")
    hh_search.add_argument("--industry", action="append")
    hh_search.add_argument("--salary", type=int)
    hh_search.add_argument("--schedule")
    hh_search.add_argument("--experience")
    hh_search.add_argument("--employment", action="append")
    hh_search.add_argument("--date-from")
    hh_search.add_argument("--date-to")
    hh_search.add_argument("--search-field", action="append")
    hh_search.add_argument("--employer-id", action="append")
    hh_search.add_argument("--excluded-employer-id", action="append")
    hh_search.add_argument("--only-with-salary", action="store_true")
    hh_search.add_argument("--limit", type=int, default=20)
    hh_search.add_argument("--page", type=int, default=0)
    hh_search.add_argument("--order-by")
    hh_search.add_argument("--period", type=int)
    hh_search.add_argument("--currency")
    hh_search.add_argument("--no-magic", action="store_true")
    hh_search.add_argument("--premium", action="store_true")

    hh_search_url = hh_sub.add_parser("search-url")
    hh_search_url_sub = hh_search_url.add_subparsers(dest="hh_search_url_command", required=True)
    hh_search_url_import = hh_search_url_sub.add_parser("import")
    hh_search_url_import.add_argument("url")
    hh_search_url_import.add_argument("--limit", type=int, default=20)

    hh_campaign = hh_sub.add_parser("campaign")
    hh_campaign_sub = hh_campaign.add_subparsers(dest="hh_campaign_command", required=True)
    hh_campaign_plan_nested = hh_campaign_sub.add_parser("plan")
    hh_campaign_plan_nested.add_argument("--limit", type=int, default=100)
    hh_campaign_plan_nested.add_argument("--min-score", type=int, default=0)
    hh_campaign_plan_nested.add_argument("--skip-tests", action="store_true")
    hh_campaign_plan_nested.add_argument("--ai-filter-mode", choices=["off", "light", "heavy"], default="off")
    hh_campaign_plan_nested.add_argument("--resume-id")
    hh_campaign_confirm_nested = hh_campaign_sub.add_parser("confirm")
    hh_campaign_confirm_nested.add_argument("--run-id", type=int, required=True)
    hh_campaign_confirm_nested.add_argument("--confirm", action="store_true")

    hh_apply = hh_sub.add_parser("apply")
    hh_apply_sub = hh_apply.add_subparsers(dest="hh_apply_command", required=True)
    hh_apply_plan_nested = hh_apply_sub.add_parser("plan")
    hh_apply_plan_nested.add_argument("--job-id", type=int, required=True)
    hh_apply_plan_nested.add_argument("--resume-id")
    hh_apply_plan_nested.add_argument("--letter", default="")
    hh_apply_plan_nested.add_argument("--letter-file", type=Path)
    hh_apply_confirm_nested = hh_apply_sub.add_parser("confirm")
    hh_apply_confirm_nested.add_argument("--plan-id", type=int, required=True)
    hh_apply_confirm_nested.add_argument("--confirm", action="store_true")

    hh_negotiations_nested = hh_sub.add_parser("negotiations")
    hh_negotiations_nested_sub = hh_negotiations_nested.add_subparsers(dest="hh_negotiations_command", required=True)
    hh_negotiations_sync_nested = hh_negotiations_nested_sub.add_parser("sync")
    hh_negotiations_sync_nested.add_argument("--status", default="active")
    hh_negotiations_nested_sub.add_parser("list")
    hh_negotiations_nested_sub.add_parser("review")

    hh_reply = hh_sub.add_parser("reply")
    hh_reply_sub = hh_reply.add_subparsers(dest="hh_reply_command", required=True)
    hh_reply_plan = hh_reply_sub.add_parser("plan")
    hh_reply_plan.add_argument("--negotiation-id", required=True)
    hh_reply_plan.add_argument("--template", default="")
    hh_reply_plan.add_argument("--status", default="active")
    hh_reply_plan.add_argument("--delay-minutes", type=int, default=0)
    hh_reply_confirm = hh_reply_sub.add_parser("confirm")
    hh_reply_confirm.add_argument("--plan-id", type=int, required=True)
    hh_reply_confirm.add_argument("--confirm", action="store_true")

    hh_web = hh_sub.add_parser("web")
    hh_web_sub = hh_web.add_subparsers(dest="hh_web_command", required=True)
    hh_web_sub.add_parser("status")
    hh_web_import = hh_web_sub.add_parser("import-cookies")
    hh_web_import.add_argument("path", type=Path)
    hh_web_search = hh_web_sub.add_parser("search-url")
    hh_web_search.add_argument("url")
    hh_web_search.add_argument("--limit", type=int, default=20)

    hh_api = hh_sub.add_parser("api")
    hh_api_sub = hh_api.add_subparsers(dest="hh_api_command", required=True)
    hh_api_call = hh_api_sub.add_parser("call")
    hh_api_call.add_argument("method")
    hh_api_call.add_argument("path")
    hh_api_call.add_argument("--data")
    hh_api_call.add_argument("--confirm", action="store_true")

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

    if args.command == "doctor":
        payload = app.doctor()
        print_json(payload) if args.json else print(_format_doctor(payload))
        return
    if args.command == "init":
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
    if args.command == "hh":
        if args.hh_command == "whoami":
            print_json(app.hh_whoami())
        elif args.hh_command == "auth":
            if args.hh_auth_command == "status":
                print_json(app.hh_auth_status())
            elif args.hh_auth_command == "whoami":
                print_json(app.hh_whoami())
            elif args.hh_auth_command == "refresh":
                print_json(mask_secrets(app.refresh_hh_token()))
            elif args.hh_auth_command == "import-token":
                print_json(
                    mask_secrets(
                        app.import_hh_token(
                            access_token=args.access_token,
                            refresh_token=args.refresh_token,
                            client_id=args.client_id,
                            client_secret=args.client_secret,
                            access_expires_at=args.access_expires_at,
                            profile=args.profile,
                        )
                    )
                )
            elif args.hh_auth_command in {"oauth-start", "oauth-callback"}:
                print_json(
                    {
                        "status": "blocked",
                        "code": "oauth_flow_not_configured",
                        "message": "Local HH OAuth flow is not configured yet. Use hh auth import-token for now.",
                        "next_actions": ["work-hunter hh auth import-token --access-token ..."],
                    }
                )
        elif args.hh_command == "account":
            if args.hh_account_command == "list":
                print_json(app.list_hh_account_profiles())
            elif args.hh_account_command == "use":
                print_json(app.use_hh_account_profile(args.name))
        elif args.hh_command == "resumes":
            if args.hh_resumes_command == "sync":
                print_json(app.sync_hh_resumes())
            elif args.hh_resumes_command == "list":
                print_json([resume.to_dict() for resume in app.storage.list_hh_resumes()])
        elif args.hh_command == "search":
            print_json(
                app.search_hh_vacancies(
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
                    order_by=args.order_by,
                    period=args.period,
                    currency=args.currency,
                    no_magic=args.no_magic,
                    premium=args.premium,
                )
            )
        elif args.hh_command == "search-url":
            if args.hh_search_url_command == "import":
                print_json(app.hh_web_search_url(args.url, limit=args.limit))
        elif args.hh_command == "campaign":
            if args.hh_campaign_command == "plan":
                print_json(
                    app.plan_hh_campaign(
                        limit=args.limit,
                        min_score=args.min_score,
                        skip_tests=args.skip_tests,
                        ai_filter_mode=args.ai_filter_mode,
                        resume_id=args.resume_id,
                    )
                )
            elif args.hh_campaign_command == "confirm":
                print_json(app.confirm_hh_campaign(args.run_id, confirm=args.confirm))
        elif args.hh_command == "apply":
            if args.hh_apply_command == "plan":
                letter = args.letter or None
                if args.letter_file:
                    letter = args.letter_file.read_text(encoding="utf-8")
                print_json(app.prepare_apply_plan(args.job_id, resume_id=args.resume_id, letter=letter))
            elif args.hh_apply_command == "confirm":
                print_json(app.confirm_apply_plan(args.plan_id, confirm=args.confirm))
        elif args.hh_command == "negotiations":
            if args.hh_negotiations_command == "sync":
                print_json(app.sync_hh_negotiations(status=args.status))
            elif args.hh_negotiations_command == "list":
                print_json([item.to_dict() for item in app.storage.list_hh_negotiations()])
            elif args.hh_negotiations_command == "review":
                print_json(
                    {
                        "status": "blocked",
                        "code": "negotiation_review_not_configured",
                        "message": "Run hh negotiations sync/list first; structured review is not configured yet.",
                    }
                )
        elif args.hh_command == "reply":
            if args.hh_reply_command == "plan":
                print_json(
                    app.plan_hh_reply(
                        negotiation_id=args.negotiation_id,
                        template=args.template,
                        status=args.status,
                        delay_minutes=args.delay_minutes,
                    )
                )
            elif args.hh_reply_command == "confirm":
                print_json(app.confirm_hh_reply(args.plan_id, confirm=args.confirm))
        elif args.hh_command == "web":
            if args.hh_web_command == "status":
                print_json(app.hh_web_status())
            elif args.hh_web_command == "import-cookies":
                print_json(app.import_hh_web_cookies(args.path))
            elif args.hh_web_command == "search-url":
                print_json(app.hh_web_search_url(args.url, limit=args.limit))
        elif args.hh_command == "api":
            if args.hh_api_command == "call":
                data = json.loads(args.data) if args.data else None
                print_json(app.hh_call_api(args.method, args.path, data=data, confirm=args.confirm))
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
        print_json(app.hh_call_api(args.method, args.path, data=data, confirm=args.confirm))
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
            )
        )
        return
    if args.command == "hh-campaign-confirm":
        print_json(app.confirm_hh_campaign(args.run_id, confirm=args.confirm))
        return
    if args.command == "digest":
        print_json(app.daily_report(limit=args.limit))
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
    if args.command == "source-capabilities":
        print_json(app.source_capabilities())
        return
    if args.command == "source":
        if args.source_command == "list":
            print_json(app.source_capabilities())
        return
    if args.command == "profile":
        if args.profile_command == "show":
            payload = app.active_profile_info()
            print_json(payload) if args.json else print(json.dumps(payload, ensure_ascii=False, indent=2))
        elif args.profile_command == "update":
            if args.file:
                data = json.loads(args.file.read_text(encoding="utf-8"))
            elif args.json:
                data = json.loads(args.json)
            else:
                parser.error("profile update requires --json or --file")
            print_json(app.update_profile(data))
        return
    if args.command == "strategy":
        if args.strategy_command == "list":
            print_json(app.list_strategies())
        elif args.strategy_command == "run":
            print_json(
                app.run_strategy(
                    args.name,
                    dry_run=args.dry_run or not args.confirm,
                    confirm=args.confirm,
                    resume_id=args.resume_id,
                )
            )
        elif args.strategy_command == "report":
            print_json(app.strategy_report(args.name))
        return
    if args.command == "export":
        if args.export_command == "jobs":
            print(app.export_jobs(status=args.status, source=args.source, format=args.format), end="")
        elif args.export_command == "applications":
            print(app.export_applications(format=args.format), end="")
        elif args.export_command == "report":
            print(app.export_report(since=args.since, format=args.format), end="")
        return
    if args.command == "import":
        if args.import_command == "jobs":
            print_json(app.import_jobs_file(args.path))
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


def _format_doctor(payload: dict[str, Any]) -> str:
    core = payload.get("core") or {}
    hh_api = payload.get("hh_api") or {}
    hh_web = payload.get("hh_web") or {}
    profile = payload.get("profile") or {}
    lines = [
        "WORK-HUNTER DOCTOR",
        "",
        "Core:",
        f"  Python: {(core.get('python') or {}).get('status', 'unknown')} {(core.get('python') or {}).get('version', '')}",
        f"  DB: {(core.get('database') or {}).get('status', 'unknown')} {(core.get('database') or {}).get('path', '')}",
        f"  Config: {(core.get('config') or {}).get('status', 'unknown')} {(core.get('config') or {}).get('path', '')}",
        "",
        "HH API:",
        f"  Auth: {hh_api.get('status', 'unknown')}",
        f"  Authorized: {hh_api.get('authorized', False)}",
        "",
        "HH Web:",
        f"  Cookies: {hh_web.get('status', 'unknown')}",
        f"  XSRF: {hh_web.get('has_xsrf', False)}",
        "",
        "Profile:",
        f"  Active: {profile.get('active', '')}",
        f"  Queries: {profile.get('queries', 0)}",
        "",
        "Actions:",
    ]
    actions = payload.get("next_actions") or []
    if actions:
        lines.extend(f"  - {action}" for action in actions)
    else:
        lines.append("  - none")
    return "\n".join(lines)


def _configure_output() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
