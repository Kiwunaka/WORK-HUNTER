from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from .models import (
    Application,
    CalendarEvent,
    HHAIDecision,
    HHAgentMCPRun,
    HHAgentEvent,
    HHAgentOutboxItem,
    HHAgentTask,
    HHAgentWebhookDelivery,
    HHApplicationAttempt,
    HHCampaignItem,
    HHCampaignRun,
    HHContact,
    HHEmployer,
    HHNegotiation,
    HHOperationLog,
    HHPendingMessage,
    HHResume,
    HHSkippedVacancy,
    HHVacancyAnalysis,
    Job,
    JobScore,
    LetterDraft,
    Resume,
    SavedSearch,
    utc_now,
)


MUTATING_SQL_RE = re.compile(
    r"\b(attach|alter|create|delete|detach|drop|insert|pragma|replace|update|vacuum)\b",
    re.IGNORECASE,
)


def _is_readonly_sql(sql: str) -> bool:
    stripped = sql.strip()
    if not stripped:
        return False
    if ";" in stripped.rstrip(";"):
        return False
    command = stripped.lstrip("(").split(None, 1)[0].lower()
    if command not in {"select", "with"}:
        return False
    return MUTATING_SQL_RE.search(stripped) is None


def _required_lastrowid(cursor: sqlite3.Cursor) -> int:
    value = cursor.lastrowid
    if value is None:
        raise RuntimeError("SQLite INSERT did not produce a row id")
    return int(value)


def _utc_cutoff_days(days: int, *, now: datetime | None = None) -> str:
    reference = now or datetime.now(timezone.utc)
    try:
        cutoff = reference - timedelta(days=max(0, int(days)))
    except OverflowError:
        cutoff = datetime.min.replace(tzinfo=timezone.utc)
    return cutoff.replace(microsecond=0).isoformat()


def _hh_api_lab_snippet_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "name": row["name"],
        "method": row["method"],
        "path": row["path"],
        "params": json.loads(row["params_json"]),
        "body": json.loads(row["body_json"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _hh_apply_from_file_state_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "source_path": row["source_path"],
        "row_key": row["row_key"],
        "vacancy_id": row["vacancy_id"],
        "resume_id": row["resume_id"],
        "status": row["status"],
        "result": json.loads(row["result_json"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _search_preset_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "name": row["name"],
        "source": row["source"],
        "params": json.loads(row["params_json"]),
        "dry_run_checked_at": row["dry_run_checked_at"],
        "last_live_run_at": row["last_live_run_at"],
        "last_result": json.loads(row["last_result_json"]),
        "enabled": bool(row["enabled"]),
    }


def _hh_agent_event_from_row(row: sqlite3.Row) -> HHAgentEvent:
    return HHAgentEvent(
        id=int(row["id"]),
        event_type=row["event_type"],
        title=row["title"],
        source_id=row["source_id"],
        payload=json.loads(row["payload_json"]),
        event_at=row["event_at"],
        status=row["status"],
        created_at=row["created_at"],
    )


def _hh_agent_task_from_row(row: sqlite3.Row) -> HHAgentTask:
    return HHAgentTask(
        id=int(row["id"]),
        task_type=row["task_type"],
        title=row["title"],
        source_id=row["source_id"],
        payload=json.loads(row["payload_json"]),
        due_at=row["due_at"],
        status=row["status"],
        created_at=row["created_at"],
    )


def _hh_agent_outbox_from_row(row: sqlite3.Row) -> HHAgentOutboxItem:
    return HHAgentOutboxItem(
        id=int(row["id"]),
        channel=row["channel"],
        target=row["target"],
        payload=json.loads(row["payload_json"]),
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _hh_agent_webhook_from_row(row: sqlite3.Row) -> HHAgentWebhookDelivery:
    return HHAgentWebhookDelivery(
        id=int(row["id"]),
        event_type=row["event_type"],
        payload=json.loads(row["payload_json"]),
        status=row["status"],
        attempts=int(row["attempts"]),
        last_error=row["last_error"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
)


MASK = "***"
SENSITIVE_EXACT = {
    "access_token",
    "refresh_token",
    "client_secret",
    "api_key",
    "authorization",
    "cookie",
    "set-cookie",
    "password",
    "token",
    "secret",
}
SENSITIVE_PARTS = ("token", "secret", "password", "cookie", "authorization", "api_key")


def redact_for_storage(value: Any) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if lowered in SENSITIVE_EXACT or any(part in lowered for part in SENSITIVE_PARTS):
                result[key] = MASK
            else:
                result[key] = redact_for_storage(item)
        return result
    if isinstance(value, list):
        return [redact_for_storage(item) for item in value]
    return value


def _json_dumps_redacted(value: Any) -> str:
    return json.dumps(redact_for_storage(value or {}), ensure_ascii=False)


BASE_SCHEMA_SQL = """
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                source_id TEXT NOT NULL,
                url TEXT NOT NULL,
                title TEXT NOT NULL,
                company TEXT NOT NULL DEFAULT '',
                salary_text TEXT NOT NULL DEFAULT '',
                salary_from INTEGER,
                salary_to INTEGER,
                currency TEXT NOT NULL DEFAULT '',
                location TEXT NOT NULL DEFAULT '',
                remote INTEGER,
                description TEXT NOT NULL DEFAULT '',
                published_at TEXT NOT NULL DEFAULT '',
                fetched_at TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'new',
                UNIQUE(source, source_id)
            );

            CREATE TABLE IF NOT EXISTS job_scores (
                job_id INTEGER NOT NULL,
                profile_id TEXT NOT NULL DEFAULT 'default',
                total_score INTEGER NOT NULL,
                title_score INTEGER NOT NULL,
                skills_score INTEGER NOT NULL,
                salary_score INTEGER NOT NULL,
                remote_score INTEGER NOT NULL,
                penalty_score INTEGER NOT NULL,
                reasons_json TEXT NOT NULL DEFAULT '[]',
                red_flags_json TEXT NOT NULL DEFAULT '[]',
                scored_at TEXT NOT NULL,
                PRIMARY KEY(job_id, profile_id),
                FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS job_status (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                changed_at TEXT NOT NULL,
                FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS letters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                template_name TEXT NOT NULL DEFAULT 'default',
                body TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS sources (
                source TEXT PRIMARY KEY,
                enabled INTEGER NOT NULL DEFAULT 1,
                last_sync_at TEXT NOT NULL DEFAULT '',
                last_error TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT NOT NULL DEFAULT '',
                count INTEGER NOT NULL DEFAULT 0,
                error TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS job_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL UNIQUE,
                body TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT '',
                FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'applied',
                notes TEXT NOT NULL DEFAULT '',
                applied_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT '',
                FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS apply_plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                source TEXT NOT NULL DEFAULT '',
                resume_id TEXT NOT NULL DEFAULT '',
                resume_hash TEXT NOT NULL DEFAULT '',
                letter TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'planned',
                risk_flags_json TEXT NOT NULL DEFAULT '[]',
                requires_confirmation INTEGER NOT NULL DEFAULT 1,
                transport TEXT NOT NULL DEFAULT '',
                raw_plan_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT '',
                confirmed_at TEXT NOT NULL DEFAULT '',
                FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS job_summaries (
                job_id INTEGER NOT NULL PRIMARY KEY,
                summary TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT '',
                FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS resumes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                body TEXT NOT NULL DEFAULT '',
                profile_id TEXT NOT NULL DEFAULT 'default',
                is_active INTEGER NOT NULL DEFAULT 0,
                ats_score INTEGER,
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS calendar_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER,
                title TEXT NOT NULL,
                event_type TEXT NOT NULL DEFAULT 'interview',
                event_date TEXT NOT NULL,
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT '',
                FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS saved_searches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                query TEXT NOT NULL DEFAULT '',
                filters_json TEXT NOT NULL DEFAULT '{}',
                alert_enabled INTEGER NOT NULL DEFAULT 0,
                last_checked_at TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS learning_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                timestamp TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS hh_resumes (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL DEFAULT '',
                url TEXT NOT NULL DEFAULT '',
                alternate_url TEXT NOT NULL DEFAULT '',
                status_id TEXT NOT NULL DEFAULT '',
                status_name TEXT NOT NULL DEFAULT '',
                can_publish_or_update INTEGER NOT NULL DEFAULT 0,
                total_views INTEGER NOT NULL DEFAULT 0,
                new_views INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS hh_employers (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL DEFAULT '',
                type TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                site_url TEXT NOT NULL DEFAULT '',
                alternate_url TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS hh_employer_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                employer_id TEXT NOT NULL DEFAULT '',
                site_url TEXT NOT NULL DEFAULT '',
                html TEXT NOT NULL DEFAULT '',
                text TEXT NOT NULL DEFAULT '',
                emails_json TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT '',
                error TEXT NOT NULL DEFAULT '',
                fetched_at TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS hh_email_followups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                employer_id TEXT NOT NULL DEFAULT '',
                employer_name TEXT NOT NULL DEFAULT '',
                to_email TEXT NOT NULL DEFAULT '',
                subject TEXT NOT NULL DEFAULT '',
                body TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT '',
                raw_result_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS hh_contacts (
                id TEXT PRIMARY KEY,
                vacancy_id TEXT NOT NULL DEFAULT '',
                employer_id TEXT NOT NULL DEFAULT '',
                employer_name TEXT NOT NULL DEFAULT '',
                name TEXT NOT NULL DEFAULT '',
                email TEXT NOT NULL DEFAULT '',
                phone_numbers TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS hh_negotiations (
                id TEXT PRIMARY KEY,
                state TEXT NOT NULL DEFAULT '',
                vacancy_id TEXT NOT NULL DEFAULT '',
                employer_id TEXT NOT NULL DEFAULT '',
                chat_id TEXT NOT NULL DEFAULT '',
                resume_id TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS hh_cleanup_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                negotiation_id TEXT NOT NULL DEFAULT '',
                action TEXT NOT NULL DEFAULT '',
                reason TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT '',
                vacancy_id TEXT NOT NULL DEFAULT '',
                employer_id TEXT NOT NULL DEFAULT '',
                raw_result_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT '',
                UNIQUE(negotiation_id, action)
            );

            CREATE TABLE IF NOT EXISTS hh_skipped_vacancies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                resume_id TEXT NOT NULL DEFAULT '',
                vacancy_id TEXT NOT NULL DEFAULT '',
                reason TEXT NOT NULL DEFAULT '',
                alternate_url TEXT NOT NULL DEFAULT '',
                name TEXT NOT NULL DEFAULT '',
                employer_name TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT '',
                UNIQUE(resume_id, vacancy_id)
            );

            CREATE TABLE IF NOT EXISTS hh_campaign_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                status TEXT NOT NULL DEFAULT 'planned',
                filters_json TEXT NOT NULL DEFAULT '{}',
                counts_json TEXT NOT NULL DEFAULT '{}',
                started_at TEXT NOT NULL,
                finished_at TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS hh_campaign_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                job_id INTEGER NOT NULL,
                vacancy_id TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'ready',
                reason TEXT NOT NULL DEFAULT '',
                resume_id TEXT NOT NULL DEFAULT '',
                letter TEXT NOT NULL DEFAULT '',
                risk_flags_json TEXT NOT NULL DEFAULT '[]',
                raw_result_json TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT NOT NULL,
                FOREIGN KEY(run_id) REFERENCES hh_campaign_runs(id) ON DELETE CASCADE,
                FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS hh_agent_mcp_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tool_name TEXT NOT NULL DEFAULT '',
                input_json TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'running',
                output_json TEXT NOT NULL DEFAULT '{}',
                error TEXT NOT NULL DEFAULT '',
                started_at TEXT NOT NULL DEFAULT '',
                finished_at TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS hh_vacancy_analysis (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER,
                vacancy_id TEXT NOT NULL DEFAULT '',
                resume_id TEXT NOT NULL DEFAULT '',
                policy_hash TEXT NOT NULL DEFAULT '',
                score INTEGER NOT NULL DEFAULT 0,
                recommended_action TEXT NOT NULL DEFAULT '',
                reasons_json TEXT NOT NULL DEFAULT '[]',
                risk_flags_json TEXT NOT NULL DEFAULT '[]',
                model TEXT NOT NULL DEFAULT '',
                raw_result_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS hh_application_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER,
                campaign_item_id INTEGER,
                vacancy_id TEXT NOT NULL DEFAULT '',
                resume_id TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT '',
                reason TEXT NOT NULL DEFAULT '',
                letter TEXT NOT NULL DEFAULT '',
                raw_result_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS hh_vacancy_response_dedup (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                resume_id TEXT NOT NULL DEFAULT '',
                dedupe_key TEXT NOT NULL DEFAULT '',
                vacancy_id TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT '',
                UNIQUE(resume_id, dedupe_key)
            );

            CREATE TABLE IF NOT EXISTS hh_pending_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action_type TEXT NOT NULL DEFAULT '',
                payload_json TEXT NOT NULL DEFAULT '{}',
                confidence REAL NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'pending',
                reason TEXT NOT NULL DEFAULT '',
                ai_decision_id INTEGER,
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS hh_ai_decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action_type TEXT NOT NULL DEFAULT '',
                target_id TEXT NOT NULL DEFAULT '',
                model TEXT NOT NULL DEFAULT '',
                policy_hash TEXT NOT NULL DEFAULT '',
                confidence REAL NOT NULL DEFAULT 0,
                reasons_json TEXT NOT NULL DEFAULT '[]',
                raw_result_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS hh_agent_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                operation TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'running',
                input_json TEXT NOT NULL DEFAULT '{}',
                counts_json TEXT NOT NULL DEFAULT '{}',
                started_at TEXT NOT NULL DEFAULT '',
                finished_at TEXT NOT NULL DEFAULT '',
                error TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS hh_operation_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                operation_id INTEGER,
                level TEXT NOT NULL DEFAULT 'info',
                message TEXT NOT NULL DEFAULT '',
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS hh_agent_outbox (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel TEXT NOT NULL DEFAULT '',
                target TEXT NOT NULL DEFAULT '',
                payload_json TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS hh_agent_webhooks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL DEFAULT '',
                payload_json TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS hh_agent_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '',
                source_id TEXT NOT NULL DEFAULT '',
                payload_json TEXT NOT NULL DEFAULT '{}',
                event_at TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS hh_agent_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_type TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '',
                source_id TEXT NOT NULL DEFAULT '',
                payload_json TEXT NOT NULL DEFAULT '{}',
                due_at TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS hh_form_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                vacancy_id TEXT NOT NULL DEFAULT '',
                resume_id TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                payload_json TEXT NOT NULL DEFAULT '{}',
                result_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS hh_personas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_id TEXT NOT NULL DEFAULT 'default',
                name TEXT NOT NULL DEFAULT '',
                body TEXT NOT NULL DEFAULT '',
                context_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS hh_apply_from_file_state (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_path TEXT NOT NULL DEFAULT '',
                row_key TEXT NOT NULL DEFAULT '',
                vacancy_id TEXT NOT NULL DEFAULT '',
                resume_id TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT '',
                result_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT '',
                UNIQUE(source_path, row_key)
            );

            CREATE TABLE IF NOT EXISTS hh_notification_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sink TEXT NOT NULL DEFAULT '',
                event_type TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT '',
                payload_json TEXT NOT NULL DEFAULT '{}',
                error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS hh_letter_templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL DEFAULT '',
                body TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT '',
                UNIQUE(name)
            );

            CREATE TABLE IF NOT EXISTS hh_employer_blacklist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                employer_id TEXT NOT NULL DEFAULT '',
                employer_name TEXT NOT NULL DEFAULT '',
                reason TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT '',
                UNIQUE(employer_id)
            );

            CREATE TABLE IF NOT EXISTS hh_api_lab_snippets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL DEFAULT '',
                method TEXT NOT NULL DEFAULT 'GET',
                path TEXT NOT NULL DEFAULT '',
                params_json TEXT NOT NULL DEFAULT '{}',
                body_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT '',
                UNIQUE(name)
            );
"""


class Storage:
    def __init__(
        self,
        path: str | Path,
        *,
        migrations_dir: str | Path | None = None,
    ) -> None:
        self.path = Path(path)
        self.migrations_dir = (
            Path(migrations_dir)
            if migrations_dir is not None
            else Path(__file__).with_name("migrations")
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        try:
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA foreign_keys = ON")
            self.conn.execute("PRAGMA busy_timeout = 5000")
            self._migrate()
        except Exception:
            self.conn.close()
            raise

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def _schema_transaction(self) -> Iterator[None]:
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield
        except Exception:
            self.conn.rollback()
            raise
        else:
            self.conn.commit()

    def _execute_sql_script(self, script: str) -> None:
        pending: list[str] = []
        for character in script:
            pending.append(character)
            if character != ";":
                continue
            candidate = "".join(pending).strip()
            if candidate and sqlite3.complete_statement(candidate):
                self.conn.execute(candidate)
                pending.clear()
        remainder = "".join(pending).strip()
        if remainder and not sqlite3.complete_statement(f"SELECT 1; {remainder}"):
            raise sqlite3.OperationalError("Incomplete SQL migration statement")

    def _migrate(self) -> None:
        with self._schema_transaction():
            self._execute_sql_script(BASE_SCHEMA_SQL)
            self._apply_migrations()
            self._ensure_backbone_columns()

    def _apply_migrations(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL
            )
            """
        )
        if not self.migrations_dir.exists():
            return
        applied = {
            row["version"]
            for row in self.conn.execute("SELECT version FROM schema_migrations").fetchall()
        }
        for path in sorted(self.migrations_dir.glob("*.sql")):
            version = path.name
            if version in applied:
                continue
            self._execute_sql_script(path.read_text(encoding="utf-8"))
            self.conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (version, utc_now()),
            )

    def _ensure_column(self, table: str, column: str, definition: str) -> None:
        columns = {
            row["name"]
            for row in self.conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in columns:
            self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def _ensure_backbone_columns(self) -> None:
        self._ensure_column("jobs", "canonical_key", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("jobs", "apply_url", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("jobs", "company_id", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("jobs", "snippet", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("jobs", "raw_json", "TEXT NOT NULL DEFAULT '{}'")
        self._ensure_column("job_scores", "seniority_score", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("job_scores", "company_score", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("job_scores", "ats_score", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("job_scores", "created_at", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("applications", "source", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("applications", "source_id", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("applications", "resume_id", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("applications", "resume_hash", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("applications", "plan_id", "INTEGER")
        self._ensure_column("applications", "transport", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("applications", "sent_at", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("applications", "result_json", "TEXT NOT NULL DEFAULT '{}'")
        self._ensure_column("applications", "error", "TEXT NOT NULL DEFAULT ''")

    def upsert_job(self, job: Job) -> int:
        self.conn.execute(
            """
            INSERT INTO jobs (
                source, source_id, url, title, company, salary_text,
                salary_from, salary_to, currency, location, remote,
                description, published_at, fetched_at, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source, source_id) DO UPDATE SET
                url = excluded.url,
                title = excluded.title,
                company = excluded.company,
                salary_text = excluded.salary_text,
                salary_from = excluded.salary_from,
                salary_to = excluded.salary_to,
                currency = excluded.currency,
                location = excluded.location,
                remote = excluded.remote,
                description = excluded.description,
                published_at = excluded.published_at,
                fetched_at = excluded.fetched_at
            """,
            (
                job.source,
                job.source_id,
                job.url,
                job.title,
                job.company,
                job.salary_text,
                job.salary_from,
                job.salary_to,
                job.currency,
                job.location,
                None if job.remote is None else int(job.remote),
                job.description,
                job.published_at,
                job.fetched_at,
                job.status,
            ),
        )
        self.conn.commit()
        row = self.conn.execute(
            "SELECT id FROM jobs WHERE source = ? AND source_id = ?",
            (job.source, job.source_id),
        ).fetchone()
        return int(row["id"])

    def upsert_jobs(self, jobs: Iterable[Job]) -> int:
        count = 0
        for job in jobs:
            self.upsert_job(job)
            count += 1
        return count

    def get_job(self, job_id: int, profile_id: str = "default") -> Job | None:
        row = self.conn.execute(
            "SELECT * FROM jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
        if row is None:
            return None
        job = self._job_from_row(row)
        job.score = self.get_score(job_id, profile_id)
        return job

    def list_jobs(
        self,
        *,
        limit: int = 100,
        status: str | None = None,
        source: str | None = None,
        min_score: int | None = None,
        profile_id: str = "default",
    ) -> list[Job]:
        conditions: list[str] = []
        params: list[Any] = []
        if status:
            conditions.append("j.status = ?")
            params.append(status)
        if source:
            conditions.append("j.source = ?")
            params.append(source)
        if min_score is not None:
            conditions.append("COALESCE(s.total_score, 0) >= ?")
            params.append(min_score)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = self.conn.execute(
            f"""
            SELECT j.*
            FROM jobs j
            LEFT JOIN job_scores s
                ON s.job_id = j.id AND s.profile_id = ?
            {where}
            ORDER BY COALESCE(s.total_score, -1) DESC, j.fetched_at DESC
            LIMIT ?
            """,
            (profile_id, *params, limit),
        ).fetchall()
        jobs = [self._job_from_row(row) for row in rows]
        for job in jobs:
            if job.id is not None:
                job.score = self.get_score(job.id, profile_id)
        return jobs

    def save_score(self, score: JobScore) -> None:
        if score.job_id is None:
            raise ValueError("score.job_id is required")
        self.conn.execute(
            """
            INSERT INTO job_scores (
                job_id, profile_id, total_score, title_score, skills_score,
                salary_score, remote_score, penalty_score, reasons_json,
                red_flags_json, scored_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_id, profile_id) DO UPDATE SET
                total_score = excluded.total_score,
                title_score = excluded.title_score,
                skills_score = excluded.skills_score,
                salary_score = excluded.salary_score,
                remote_score = excluded.remote_score,
                penalty_score = excluded.penalty_score,
                reasons_json = excluded.reasons_json,
                red_flags_json = excluded.red_flags_json,
                scored_at = excluded.scored_at
            """,
            (
                score.job_id,
                score.profile_id,
                score.total_score,
                score.title_score,
                score.skills_score,
                score.salary_score,
                score.remote_score,
                score.penalty_score,
                json.dumps(score.reasons, ensure_ascii=False),
                json.dumps(score.red_flags, ensure_ascii=False),
                score.scored_at,
            ),
        )
        self.conn.commit()

    def get_score(self, job_id: int, profile_id: str = "default") -> JobScore | None:
        row = self.conn.execute(
            "SELECT * FROM job_scores WHERE job_id = ? AND profile_id = ?",
            (job_id, profile_id),
        ).fetchone()
        if row is None:
            return None
        return JobScore(
            job_id=int(row["job_id"]),
            profile_id=row["profile_id"],
            total_score=int(row["total_score"]),
            title_score=int(row["title_score"]),
            skills_score=int(row["skills_score"]),
            salary_score=int(row["salary_score"]),
            remote_score=int(row["remote_score"]),
            penalty_score=int(row["penalty_score"]),
            reasons=json.loads(row["reasons_json"]),
            red_flags=json.loads(row["red_flags_json"]),
            scored_at=row["scored_at"],
        )

    def set_status(self, job_id: int, status: str, note: str = "") -> None:
        now = utc_now()
        self.conn.execute(
            "UPDATE jobs SET status = ? WHERE id = ?",
            (status, job_id),
        )
        self.conn.execute(
            """
            INSERT INTO job_status (job_id, status, note, changed_at)
            VALUES (?, ?, ?, ?)
            """,
            (job_id, status, note, now),
        )
        self.conn.commit()

    def save_letter(self, draft: LetterDraft) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO letters (job_id, template_name, body, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (draft.job_id, draft.template_name, draft.body, draft.created_at),
        )
        self.conn.commit()
        return _required_lastrowid(cur)

    def get_latest_letter(self, job_id: int) -> LetterDraft | None:
        row = self.conn.execute(
            """
            SELECT * FROM letters
            WHERE job_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (job_id,),
        ).fetchone()
        if row is None:
            return None
        return LetterDraft(
            job_id=int(row["job_id"]),
            template_name=row["template_name"],
            body=row["body"],
            created_at=row["created_at"],
        )

    def record_source(
        self,
        source: str,
        *,
        enabled: bool = True,
        last_error: str = "",
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO sources (source, enabled, last_sync_at, last_error)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(source) DO UPDATE SET
                enabled = excluded.enabled,
                last_sync_at = excluded.last_sync_at,
                last_error = excluded.last_error
            """,
            (source, int(enabled), utc_now(), last_error),
        )
        self.conn.commit()

    def list_sources(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT source, enabled, last_sync_at, last_error FROM sources ORDER BY source"
        ).fetchall()
        return [
            {
                "source": row["source"],
                "enabled": bool(row["enabled"]),
                "last_sync_at": row["last_sync_at"],
                "last_error": row["last_error"],
            }
            for row in rows
        ]

    def query_readonly(self, sql: str) -> list[dict[str, Any]]:
        if not _is_readonly_sql(sql):
            raise ValueError("Only read-only SELECT/WITH queries are allowed.")
        rows = self.conn.execute(sql).fetchall()
        return [dict(row) for row in rows]

    def start_run(self, kind: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO runs (kind, started_at) VALUES (?, ?)",
            (kind, utc_now()),
        )
        self.conn.commit()
        return _required_lastrowid(cur)

    def finish_run(self, run_id: int, *, count: int = 0, error: str = "") -> None:
        self.conn.execute(
            "UPDATE runs SET finished_at = ?, count = ?, error = ? WHERE id = ?",
            (utc_now(), count, error, run_id),
        )
        self.conn.commit()

    def save_note(self, job_id: int, body: str) -> None:
        now = utc_now()
        self.conn.execute(
            """
            INSERT INTO job_notes (job_id, body, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                body = excluded.body,
                updated_at = excluded.updated_at
            """,
            (job_id, body, now, now),
        )
        self.conn.commit()

    def get_note(self, job_id: int) -> str:
        row = self.conn.execute(
            "SELECT body FROM job_notes WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        return row["body"] if row else ""

    def save_application(
        self,
        job_id: int,
        status: str = "applied",
        notes: str = "",
        *,
        source: str = "",
        source_id: str = "",
        resume_id: str = "",
        resume_hash: str = "",
        plan_id: int | None = None,
        transport: str = "",
        sent_at: str = "",
        result: dict[str, Any] | None = None,
        error: str = "",
    ) -> None:
        now = utc_now()
        if not (source and source_id):
            job = self.get_job(job_id)
            if job:
                source = source or job.source
                source_id = source_id or job.source_id
        sent_at = sent_at or (now if status in {"applied", "sent"} else "")
        self.conn.execute(
            """
            INSERT INTO applications (
                job_id, status, notes, applied_at, updated_at,
                source, source_id, resume_id, resume_hash, plan_id,
                transport, sent_at, result_json, error
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                status = excluded.status,
                notes = excluded.notes,
                applied_at = CASE
                    WHEN applications.status != 'applied'
                         AND excluded.status = 'applied'
                    THEN excluded.applied_at
                    ELSE applications.applied_at
                END,
                updated_at = excluded.updated_at,
                source = excluded.source,
                source_id = excluded.source_id,
                resume_id = excluded.resume_id,
                resume_hash = excluded.resume_hash,
                plan_id = excluded.plan_id,
                transport = excluded.transport,
                sent_at = excluded.sent_at,
                result_json = excluded.result_json,
                error = excluded.error
            """,
            (
                job_id,
                status,
                notes,
                now,
                now,
                source,
                source_id,
                resume_id,
                resume_hash,
                plan_id,
                transport,
                sent_at,
                _json_dumps_redacted(result or {}),
                error,
            ),
        )
        self.conn.commit()

    def save_apply_plan(self, plan: dict[str, Any]) -> int:
        now = utc_now()
        risk_flags = plan.get("risk_flags") or []
        raw_plan = dict(plan)
        raw_plan.pop("id", None)
        raw_plan.pop("plan_id", None)
        cur = self.conn.execute(
            """
            INSERT INTO apply_plans (
                job_id, source, resume_id, resume_hash, letter, status,
                risk_flags_json, requires_confirmation, transport,
                raw_plan_json, created_at, confirmed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(plan.get("job_id") or 0),
                str(plan.get("source") or ""),
                str(plan.get("resume_id") or ""),
                str(plan.get("resume_hash") or ""),
                str(plan.get("letter") or ""),
                str(plan.get("status") or "planned"),
                json.dumps(risk_flags, ensure_ascii=False),
                int(bool(plan.get("requires_confirmation", True))),
                str(plan.get("mode") or plan.get("transport") or ""),
                _json_dumps_redacted(raw_plan),
                now,
                "",
            ),
        )
        self.conn.commit()
        return _required_lastrowid(cur)

    def get_apply_plan(self, plan_id: int) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM apply_plans WHERE id = ?",
            (plan_id,),
        ).fetchone()
        if row is None:
            return None
        raw = json.loads(row["raw_plan_json"])
        raw.update(
            {
                "id": int(row["id"]),
                "plan_id": int(row["id"]),
                "job_id": int(row["job_id"]),
                "source": row["source"],
                "resume_id": row["resume_id"],
                "resume_hash": row["resume_hash"],
                "letter": row["letter"],
                "status": row["status"],
                "risk_flags": json.loads(row["risk_flags_json"]),
                "requires_confirmation": bool(row["requires_confirmation"]),
                "transport": row["transport"],
                "created_at": row["created_at"],
                "confirmed_at": row["confirmed_at"],
            }
        )
        return raw

    def update_apply_plan_status(self, plan_id: int, status: str, *, confirmed: bool = False) -> None:
        confirmed_at = utc_now() if confirmed else ""
        self.conn.execute(
            """
            UPDATE apply_plans
            SET status = ?,
                confirmed_at = CASE WHEN ? != '' THEN ? ELSE confirmed_at END
            WHERE id = ?
            """,
            (status, confirmed_at, confirmed_at, plan_id),
        )
        self.conn.commit()

    def get_application(self, job_id: int) -> Application | None:
        row = self.conn.execute(
            "SELECT * FROM applications WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        if row is None:
            return None
        return Application(
            id=int(row["id"]),
            job_id=int(row["job_id"]),
            status=row["status"],
            notes=row["notes"],
            applied_at=row["applied_at"],
            updated_at=row["updated_at"],
        )

    def list_applications(self) -> list[Application]:
        rows = self.conn.execute(
            "SELECT * FROM applications ORDER BY applied_at DESC"
        ).fetchall()
        return [
            Application(
                id=int(row["id"]),
                job_id=int(row["job_id"]),
                status=row["status"],
                notes=row["notes"],
                applied_at=row["applied_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]

    def upsert_hh_resume(self, resume: HHResume) -> None:
        self.conn.execute(
            """
            INSERT INTO hh_resumes (
                id, title, url, alternate_url, status_id, status_name,
                can_publish_or_update, total_views, new_views, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                title = excluded.title,
                url = excluded.url,
                alternate_url = excluded.alternate_url,
                status_id = excluded.status_id,
                status_name = excluded.status_name,
                can_publish_or_update = excluded.can_publish_or_update,
                total_views = excluded.total_views,
                new_views = excluded.new_views,
                updated_at = excluded.updated_at
            """,
            (
                resume.id,
                resume.title,
                resume.url,
                resume.alternate_url,
                resume.status_id,
                resume.status_name,
                int(resume.can_publish_or_update),
                resume.total_views,
                resume.new_views,
                resume.updated_at,
            ),
        )
        self.conn.commit()

    def list_hh_resumes(self) -> list[HHResume]:
        rows = self.conn.execute(
            "SELECT * FROM hh_resumes ORDER BY id"
        ).fetchall()
        return [
            HHResume(
                id=row["id"],
                title=row["title"],
                url=row["url"],
                alternate_url=row["alternate_url"],
                status_id=row["status_id"],
                status_name=row["status_name"],
                can_publish_or_update=bool(row["can_publish_or_update"]),
                total_views=int(row["total_views"]),
                new_views=int(row["new_views"]),
                updated_at=row["updated_at"],
            )
            for row in rows
        ]

    def upsert_hh_employer(self, employer: HHEmployer) -> None:
        self.conn.execute(
            """
            INSERT INTO hh_employers (
                id, name, type, description, site_url, alternate_url, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                type = excluded.type,
                description = excluded.description,
                site_url = excluded.site_url,
                alternate_url = excluded.alternate_url,
                updated_at = excluded.updated_at
            """,
            (
                employer.id,
                employer.name,
                employer.type,
                employer.description,
                employer.site_url,
                employer.alternate_url,
                employer.updated_at,
            ),
        )
        self.conn.commit()

    def list_hh_employers(self) -> list[HHEmployer]:
        rows = self.conn.execute("SELECT * FROM hh_employers ORDER BY id").fetchall()
        return [
            HHEmployer(
                id=row["id"],
                name=row["name"],
                type=row["type"],
                description=row["description"],
                site_url=row["site_url"],
                alternate_url=row["alternate_url"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]

    def save_hh_employer_snapshot(
        self,
        *,
        employer_id: str,
        site_url: str,
        html: str,
        text: str,
        emails: list[str],
        status: str,
        error: str = "",
    ) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO hh_employer_snapshots (
                employer_id, site_url, html, text, emails_json, status, error, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                employer_id,
                site_url,
                html,
                text,
                json.dumps(emails, ensure_ascii=False),
                status,
                error,
                utc_now(),
            ),
        )
        self.conn.commit()
        return _required_lastrowid(cur)

    def list_hh_employer_snapshots(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM hh_employer_snapshots ORDER BY id"
        ).fetchall()
        return [
            {
                "id": int(row["id"]),
                "employer_id": row["employer_id"],
                "site_url": row["site_url"],
                "html": row["html"],
                "text": row["text"],
                "emails": json.loads(row["emails_json"]),
                "status": row["status"],
                "error": row["error"],
                "fetched_at": row["fetched_at"],
            }
            for row in rows
        ]

    def save_hh_email_followup(
        self,
        *,
        employer_id: str,
        employer_name: str,
        to_email: str,
        subject: str,
        body: str,
        status: str,
        raw_result: dict[str, Any] | None = None,
    ) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO hh_email_followups (
                employer_id, employer_name, to_email, subject, body, status,
                raw_result_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                employer_id,
                employer_name,
                to_email,
                subject,
                body,
                status,
                json.dumps(raw_result or {}, ensure_ascii=False),
                utc_now(),
            ),
        )
        self.conn.commit()
        return _required_lastrowid(cur)

    def list_hh_email_followups(self) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM hh_email_followups ORDER BY id").fetchall()
        return [
            {
                "id": int(row["id"]),
                "employer_id": row["employer_id"],
                "employer_name": row["employer_name"],
                "to_email": row["to_email"],
                "subject": row["subject"],
                "body": row["body"],
                "status": row["status"],
                "raw_result": json.loads(row["raw_result_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def upsert_hh_contact(self, contact: HHContact) -> None:
        contact_id = contact.id or f"{contact.vacancy_id}:{contact.email}:{contact.name}"
        self.conn.execute(
            """
            INSERT INTO hh_contacts (
                id, vacancy_id, employer_id, employer_name, name, email,
                phone_numbers, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                vacancy_id = excluded.vacancy_id,
                employer_id = excluded.employer_id,
                employer_name = excluded.employer_name,
                name = excluded.name,
                email = excluded.email,
                phone_numbers = excluded.phone_numbers,
                updated_at = excluded.updated_at
            """,
            (
                contact_id,
                contact.vacancy_id,
                contact.employer_id,
                contact.employer_name,
                contact.name,
                contact.email,
                contact.phone_numbers,
                contact.updated_at,
            ),
        )
        self.conn.commit()

    def list_hh_contacts(self) -> list[HHContact]:
        rows = self.conn.execute("SELECT * FROM hh_contacts ORDER BY id").fetchall()
        return [
            HHContact(
                id=row["id"],
                vacancy_id=row["vacancy_id"],
                employer_id=row["employer_id"],
                employer_name=row["employer_name"],
                name=row["name"],
                email=row["email"],
                phone_numbers=row["phone_numbers"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]

    def upsert_hh_negotiation(self, negotiation: HHNegotiation) -> None:
        self.conn.execute(
            """
            INSERT INTO hh_negotiations (
                id, state, vacancy_id, employer_id, chat_id, resume_id, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                state = excluded.state,
                vacancy_id = excluded.vacancy_id,
                employer_id = excluded.employer_id,
                chat_id = excluded.chat_id,
                resume_id = excluded.resume_id,
                updated_at = excluded.updated_at
            """,
            (
                negotiation.id,
                negotiation.state,
                negotiation.vacancy_id,
                negotiation.employer_id,
                negotiation.chat_id,
                negotiation.resume_id,
                negotiation.updated_at,
            ),
        )
        self.conn.commit()

    def list_hh_negotiations(self) -> list[HHNegotiation]:
        rows = self.conn.execute("SELECT * FROM hh_negotiations ORDER BY id").fetchall()
        return [
            HHNegotiation(
                id=row["id"],
                state=row["state"],
                vacancy_id=row["vacancy_id"],
                employer_id=row["employer_id"],
                chat_id=row["chat_id"],
                resume_id=row["resume_id"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]

    def save_hh_cleanup_event(
        self,
        *,
        negotiation_id: str,
        action: str,
        reason: str,
        status: str,
        vacancy_id: str = "",
        employer_id: str = "",
        raw_result: dict[str, Any] | None = None,
    ) -> None:
        now = utc_now()
        self.conn.execute(
            """
            INSERT INTO hh_cleanup_events (
                negotiation_id, action, reason, status, vacancy_id, employer_id,
                raw_result_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(negotiation_id, action) DO UPDATE SET
                reason = excluded.reason,
                status = excluded.status,
                vacancy_id = excluded.vacancy_id,
                employer_id = excluded.employer_id,
                raw_result_json = excluded.raw_result_json,
                updated_at = excluded.updated_at
            """,
            (
                negotiation_id,
                action,
                reason,
                status,
                vacancy_id,
                employer_id,
                json.dumps(raw_result or {}, ensure_ascii=False),
                now,
                now,
            ),
        )
        self.conn.commit()

    def list_hh_cleanup_events(self) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM hh_cleanup_events ORDER BY id").fetchall()
        return [
            {
                "id": int(row["id"]),
                "negotiation_id": row["negotiation_id"],
                "action": row["action"],
                "reason": row["reason"],
                "status": row["status"],
                "vacancy_id": row["vacancy_id"],
                "employer_id": row["employer_id"],
                "raw_result": json.loads(row["raw_result_json"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def save_hh_skipped_vacancy(
        self,
        *,
        resume_id: str,
        vacancy_id: str,
        reason: str,
        alternate_url: str = "",
        name: str = "",
        employer_name: str = "",
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO hh_skipped_vacancies (
                resume_id, vacancy_id, reason, alternate_url, name, employer_name, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(resume_id, vacancy_id) DO UPDATE SET
                reason = excluded.reason,
                alternate_url = excluded.alternate_url,
                name = excluded.name,
                employer_name = excluded.employer_name,
                created_at = excluded.created_at
            """,
            (resume_id, vacancy_id, reason, alternate_url, name, employer_name, utc_now()),
        )
        self.conn.commit()

    def list_hh_skipped_vacancies(self) -> list[HHSkippedVacancy]:
        rows = self.conn.execute(
            "SELECT * FROM hh_skipped_vacancies ORDER BY id"
        ).fetchall()
        return [
            HHSkippedVacancy(
                id=int(row["id"]),
                resume_id=row["resume_id"],
                vacancy_id=row["vacancy_id"],
                reason=row["reason"],
                alternate_url=row["alternate_url"],
                name=row["name"],
                employer_name=row["employer_name"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def clear_hh_skipped_vacancies(self) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS cnt FROM hh_skipped_vacancies"
        ).fetchone()
        count = int(row["cnt"])
        self.conn.execute("DELETE FROM hh_skipped_vacancies")
        self.conn.commit()
        return count

    def create_hh_campaign_run(self, *, filters: dict[str, Any] | None = None) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO hh_campaign_runs (status, filters_json, counts_json, started_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                "planned",
                json.dumps(filters or {}, ensure_ascii=False),
                json.dumps({}, ensure_ascii=False),
                utc_now(),
            ),
        )
        self.conn.commit()
        return _required_lastrowid(cur)

    def update_hh_campaign_run(
        self,
        run_id: int,
        *,
        status: str,
        counts: dict[str, int],
        finished: bool = False,
    ) -> None:
        finished_at = utc_now() if finished else ""
        self.conn.execute(
            """
            UPDATE hh_campaign_runs
            SET status = ?, counts_json = ?, finished_at = ?
            WHERE id = ?
            """,
            (status, json.dumps(counts, ensure_ascii=False), finished_at, run_id),
        )
        self.conn.commit()

    def get_hh_campaign_run(self, run_id: int) -> HHCampaignRun | None:
        row = self.conn.execute(
            "SELECT * FROM hh_campaign_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            return None
        return HHCampaignRun(
            id=int(row["id"]),
            status=row["status"],
            filters=json.loads(row["filters_json"]),
            counts=json.loads(row["counts_json"]),
            started_at=row["started_at"],
            finished_at=row["finished_at"],
        )

    def save_hh_campaign_item(self, item: HHCampaignItem) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO hh_campaign_items (
                run_id, job_id, vacancy_id, status, reason, resume_id, letter,
                risk_flags_json, raw_result_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.run_id,
                item.job_id,
                item.vacancy_id,
                item.status,
                item.reason,
                item.resume_id,
                item.letter,
                json.dumps(item.risk_flags, ensure_ascii=False),
                json.dumps(item.raw_result, ensure_ascii=False),
                item.updated_at,
            ),
        )
        self.conn.commit()
        return _required_lastrowid(cur)

    def update_hh_campaign_item(
        self,
        item_id: int,
        *,
        status: str,
        reason: str = "",
        raw_result: dict[str, Any] | None = None,
    ) -> None:
        self.conn.execute(
            """
            UPDATE hh_campaign_items
            SET status = ?, reason = ?, raw_result_json = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                status,
                reason,
                json.dumps(raw_result or {}, ensure_ascii=False),
                utc_now(),
                item_id,
            ),
        )
        self.conn.commit()

    def list_hh_campaign_items(self, run_id: int) -> list[HHCampaignItem]:
        rows = self.conn.execute(
            """
            SELECT * FROM hh_campaign_items
            WHERE run_id = ?
            ORDER BY id
            """,
            (run_id,),
        ).fetchall()
        return [
            HHCampaignItem(
                id=int(row["id"]),
                run_id=int(row["run_id"]),
                job_id=int(row["job_id"]),
                vacancy_id=row["vacancy_id"],
                status=row["status"],
                reason=row["reason"],
                resume_id=row["resume_id"],
                letter=row["letter"],
                risk_flags=json.loads(row["risk_flags_json"]),
                raw_result=json.loads(row["raw_result_json"]),
                updated_at=row["updated_at"],
            )
            for row in rows
        ]

    def start_hh_agent_mcp_run(self, tool_name: str, input_data: dict[str, Any] | None = None) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO hh_agent_mcp_runs (tool_name, input_json, status, started_at)
            VALUES (?, ?, ?, ?)
            """,
            (tool_name, _json_dumps_redacted(input_data or {}), "running", utc_now()),
        )
        self.conn.commit()
        return _required_lastrowid(cur)

    def finish_hh_agent_mcp_run(
        self,
        run_id: int,
        *,
        status: str = "ok",
        output: dict[str, Any] | None = None,
        error: str = "",
    ) -> None:
        self.conn.execute(
            """
            UPDATE hh_agent_mcp_runs
            SET status = ?, output_json = ?, error = ?, finished_at = ?
            WHERE id = ?
            """,
            (status, _json_dumps_redacted(output or {}), error, utc_now(), run_id),
        )
        self.conn.commit()

    def list_hh_agent_mcp_runs(self) -> list[HHAgentMCPRun]:
        rows = self.conn.execute("SELECT * FROM hh_agent_mcp_runs ORDER BY id").fetchall()
        return [
            HHAgentMCPRun(
                id=int(row["id"]),
                tool_name=row["tool_name"],
                input=json.loads(row["input_json"]),
                status=row["status"],
                output=json.loads(row["output_json"]),
                error=row["error"],
                started_at=row["started_at"],
                finished_at=row["finished_at"],
            )
            for row in rows
        ]

    def upsert_hh_agent_event(self, event: HHAgentEvent) -> int:
        now = event.created_at or utc_now()
        existing = self.conn.execute(
            """
            SELECT id FROM hh_agent_events
            WHERE source_id = ? AND event_type = ? AND title = ? AND event_at = ?
            """,
            (event.source_id, event.event_type, event.title, event.event_at),
        ).fetchone()
        if existing:
            self.conn.execute(
                """
                UPDATE hh_agent_events
                SET payload_json = ?, status = ?
                WHERE id = ?
                """,
                (json.dumps(event.payload, ensure_ascii=False), event.status, int(existing["id"])),
            )
            self.conn.commit()
            return int(existing["id"])
        cur = self.conn.execute(
            """
            INSERT INTO hh_agent_events (
                event_type, title, source_id, payload_json, event_at, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_type,
                event.title,
                event.source_id,
                json.dumps(event.payload, ensure_ascii=False),
                event.event_at,
                event.status,
                now,
            ),
        )
        self.conn.commit()
        return _required_lastrowid(cur)

    def list_hh_agent_events(
        self,
        *,
        status: str | None = None,
        limit: int | None = None,
    ) -> list[HHAgentEvent]:
        params: list[Any] = []
        where = ""
        if status:
            where = "WHERE status = ?"
            params.append(status)
        limit_sql = ""
        if limit is not None:
            limit_sql = " LIMIT ?"
            params.append(max(1, int(limit)))
        rows = self.conn.execute(
            f"SELECT * FROM hh_agent_events {where} ORDER BY id ASC{limit_sql}",
            params,
        ).fetchall()
        return [_hh_agent_event_from_row(row) for row in rows]

    def upsert_hh_agent_task(self, task: HHAgentTask) -> int:
        now = task.created_at or utc_now()
        existing = self.conn.execute(
            """
            SELECT id FROM hh_agent_tasks
            WHERE source_id = ? AND task_type = ? AND title = ? AND due_at = ?
            """,
            (task.source_id, task.task_type, task.title, task.due_at),
        ).fetchone()
        if existing:
            self.conn.execute(
                """
                UPDATE hh_agent_tasks
                SET payload_json = ?, status = ?
                WHERE id = ?
                """,
                (json.dumps(task.payload, ensure_ascii=False), task.status, int(existing["id"])),
            )
            self.conn.commit()
            return int(existing["id"])
        cur = self.conn.execute(
            """
            INSERT INTO hh_agent_tasks (
                task_type, title, source_id, payload_json, due_at, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task.task_type,
                task.title,
                task.source_id,
                json.dumps(task.payload, ensure_ascii=False),
                task.due_at,
                task.status,
                now,
            ),
        )
        self.conn.commit()
        return _required_lastrowid(cur)

    def list_hh_agent_tasks(
        self,
        *,
        status: str | None = None,
        limit: int | None = None,
    ) -> list[HHAgentTask]:
        params: list[Any] = []
        where = ""
        if status:
            where = "WHERE status = ?"
            params.append(status)
        limit_sql = ""
        if limit is not None:
            limit_sql = " LIMIT ?"
            params.append(max(1, int(limit)))
        rows = self.conn.execute(
            f"SELECT * FROM hh_agent_tasks {where} ORDER BY due_at ASC, id ASC{limit_sql}",
            params,
        ).fetchall()
        return [_hh_agent_task_from_row(row) for row in rows]

    def create_hh_agent_outbox(
        self,
        *,
        channel: str,
        target: str,
        payload: dict[str, Any] | None = None,
        status: str = "pending",
    ) -> int:
        now = utc_now()
        cur = self.conn.execute(
            """
            INSERT INTO hh_agent_outbox (
                channel, target, payload_json, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                channel,
                target,
                _json_dumps_redacted(payload or {}),
                status,
                now,
                now,
            ),
        )
        self.conn.commit()
        return _required_lastrowid(cur)

    def update_hh_agent_outbox(
        self,
        item_id: int,
        *,
        status: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        current = self.conn.execute(
            "SELECT payload_json FROM hh_agent_outbox WHERE id = ?",
            (item_id,),
        ).fetchone()
        payload_json = current["payload_json"] if current and payload is None else _json_dumps_redacted(payload or {})
        self.conn.execute(
            """
            UPDATE hh_agent_outbox
            SET status = ?, payload_json = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, payload_json, utc_now(), item_id),
        )
        self.conn.commit()

    def list_hh_agent_outbox(
        self,
        *,
        status: str | None = None,
        channel: str | None = None,
        limit: int | None = None,
    ) -> list[HHAgentOutboxItem]:
        conditions: list[str] = []
        params: list[Any] = []
        if status:
            conditions.append("status = ?")
            params.append(status)
        if channel:
            conditions.append("channel = ?")
            params.append(channel)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        limit_sql = ""
        if limit is not None:
            limit_sql = " LIMIT ?"
            params.append(max(1, int(limit)))
        rows = self.conn.execute(
            f"SELECT * FROM hh_agent_outbox {where} ORDER BY id ASC{limit_sql}",
            params,
        ).fetchall()
        return [_hh_agent_outbox_from_row(row) for row in rows]

    def get_hh_agent_outbox(self, item_id: int) -> HHAgentOutboxItem | None:
        row = self.conn.execute(
            "SELECT * FROM hh_agent_outbox WHERE id = ?",
            (item_id,),
        ).fetchone()
        return _hh_agent_outbox_from_row(row) if row else None

    def create_hh_agent_webhook(
        self,
        *,
        event_type: str,
        payload: dict[str, Any] | None = None,
        status: str = "pending",
    ) -> int:
        now = utc_now()
        cur = self.conn.execute(
            """
            INSERT INTO hh_agent_webhooks (
                event_type, payload_json, status, attempts, last_error, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_type,
                _json_dumps_redacted(payload or {}),
                status,
                0,
                "",
                now,
                now,
            ),
        )
        self.conn.commit()
        return _required_lastrowid(cur)

    def update_hh_agent_webhook(
        self,
        delivery_id: int,
        *,
        status: str,
        attempts: int,
        last_error: str = "",
        payload: dict[str, Any] | None = None,
    ) -> None:
        current = self.conn.execute(
            "SELECT payload_json FROM hh_agent_webhooks WHERE id = ?",
            (delivery_id,),
        ).fetchone()
        payload_json = current["payload_json"] if current and payload is None else _json_dumps_redacted(payload or {})
        self.conn.execute(
            """
            UPDATE hh_agent_webhooks
            SET status = ?, attempts = ?, last_error = ?, payload_json = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, attempts, last_error, payload_json, utc_now(), delivery_id),
        )
        self.conn.commit()

    def list_hh_agent_webhooks(
        self,
        *,
        status: str | None = None,
        limit: int | None = None,
    ) -> list[HHAgentWebhookDelivery]:
        params: list[Any] = []
        where = ""
        if status:
            where = "WHERE status = ?"
            params.append(status)
        limit_sql = ""
        if limit is not None:
            limit_sql = " LIMIT ?"
            params.append(max(1, int(limit)))
        rows = self.conn.execute(
            f"SELECT * FROM hh_agent_webhooks {where} ORDER BY id ASC{limit_sql}",
            params,
        ).fetchall()
        return [_hh_agent_webhook_from_row(row) for row in rows]

    def save_hh_vacancy_analysis(self, analysis: HHVacancyAnalysis) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO hh_vacancy_analysis (
                run_id, vacancy_id, resume_id, policy_hash, score, recommended_action,
                reasons_json, risk_flags_json, model, raw_result_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                analysis.run_id,
                analysis.vacancy_id,
                analysis.resume_id,
                analysis.policy_hash,
                analysis.score,
                analysis.recommended_action,
                json.dumps(analysis.reasons, ensure_ascii=False),
                json.dumps(analysis.risk_flags, ensure_ascii=False),
                analysis.model,
                json.dumps(analysis.raw_result, ensure_ascii=False),
                analysis.created_at,
            ),
        )
        self.conn.commit()
        return _required_lastrowid(cur)

    def list_hh_vacancy_analysis(self, vacancy_id: str | None = None) -> list[HHVacancyAnalysis]:
        if vacancy_id:
            rows = self.conn.execute(
                "SELECT * FROM hh_vacancy_analysis WHERE vacancy_id = ? ORDER BY id",
                (vacancy_id,),
            ).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM hh_vacancy_analysis ORDER BY id").fetchall()
        return [
            HHVacancyAnalysis(
                id=int(row["id"]),
                run_id=None if row["run_id"] is None else int(row["run_id"]),
                vacancy_id=row["vacancy_id"],
                resume_id=row["resume_id"],
                policy_hash=row["policy_hash"],
                score=int(row["score"]),
                recommended_action=row["recommended_action"],
                reasons=json.loads(row["reasons_json"]),
                risk_flags=json.loads(row["risk_flags_json"]),
                model=row["model"],
                raw_result=json.loads(row["raw_result_json"]),
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def save_hh_application_attempt(self, attempt: HHApplicationAttempt) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO hh_application_attempts (
                run_id, campaign_item_id, vacancy_id, resume_id, status, reason,
                letter, raw_result_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                attempt.run_id,
                attempt.campaign_item_id,
                attempt.vacancy_id,
                attempt.resume_id,
                attempt.status,
                attempt.reason,
                attempt.letter,
                json.dumps(attempt.raw_result, ensure_ascii=False),
                attempt.created_at,
            ),
        )
        self.conn.commit()
        return _required_lastrowid(cur)

    def list_hh_application_attempts(self, vacancy_id: str | None = None) -> list[HHApplicationAttempt]:
        if vacancy_id:
            rows = self.conn.execute(
                "SELECT * FROM hh_application_attempts WHERE vacancy_id = ? ORDER BY id",
                (vacancy_id,),
            ).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM hh_application_attempts ORDER BY id").fetchall()
        return [
            HHApplicationAttempt(
                id=int(row["id"]),
                run_id=None if row["run_id"] is None else int(row["run_id"]),
                campaign_item_id=None if row["campaign_item_id"] is None else int(row["campaign_item_id"]),
                vacancy_id=row["vacancy_id"],
                resume_id=row["resume_id"],
                status=row["status"],
                reason=row["reason"],
                letter=row["letter"],
                raw_result=json.loads(row["raw_result_json"]),
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def save_hh_response_dedupe(self, *, resume_id: str, dedupe_key: str, vacancy_id: str) -> int:
        self.conn.execute(
            """
            INSERT INTO hh_vacancy_response_dedup (
                resume_id, dedupe_key, vacancy_id, created_at
            ) VALUES (?, ?, ?, ?)
            ON CONFLICT(resume_id, dedupe_key) DO UPDATE SET
                vacancy_id = excluded.vacancy_id
            """,
            (resume_id, dedupe_key, vacancy_id, utc_now()),
        )
        self.conn.commit()
        row = self.conn.execute(
            """
            SELECT id FROM hh_vacancy_response_dedup
            WHERE resume_id = ? AND dedupe_key = ?
            """,
            (resume_id, dedupe_key),
        ).fetchone()
        return int(row["id"])

    def get_hh_response_dedupe(self, *, resume_id: str, dedupe_key: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT * FROM hh_vacancy_response_dedup
            WHERE resume_id = ? AND dedupe_key = ?
            """,
            (resume_id, dedupe_key),
        ).fetchone()
        return dict(row) if row else None

    def save_hh_ai_decision(self, decision: HHAIDecision) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO hh_ai_decisions (
                action_type, target_id, model, policy_hash, confidence,
                reasons_json, raw_result_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                decision.action_type,
                decision.target_id,
                decision.model,
                decision.policy_hash,
                decision.confidence,
                json.dumps(decision.reasons, ensure_ascii=False),
                _json_dumps_redacted(decision.raw_result),
                decision.created_at,
            ),
        )
        self.conn.commit()
        return _required_lastrowid(cur)

    def list_hh_ai_decisions(self) -> list[HHAIDecision]:
        rows = self.conn.execute("SELECT * FROM hh_ai_decisions ORDER BY id").fetchall()
        return [
            HHAIDecision(
                id=int(row["id"]),
                action_type=row["action_type"],
                target_id=row["target_id"],
                model=row["model"],
                policy_hash=row["policy_hash"],
                confidence=float(row["confidence"]),
                reasons=json.loads(row["reasons_json"]),
                raw_result=json.loads(row["raw_result_json"]),
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def create_hh_pending_message(self, message: HHPendingMessage) -> int:
        now = message.created_at or utc_now()
        cur = self.conn.execute(
            """
            INSERT INTO hh_pending_messages (
                action_type, payload_json, confidence, status, reason,
                ai_decision_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                message.action_type,
                json.dumps(message.payload, ensure_ascii=False),
                message.confidence,
                message.status,
                message.reason,
                message.ai_decision_id,
                now,
                message.updated_at or now,
            ),
        )
        self.conn.commit()
        return _required_lastrowid(cur)

    def update_hh_pending_message(
        self,
        message_id: int,
        *,
        status: str,
        reason: str = "",
        payload: dict[str, Any] | None = None,
    ) -> None:
        current = self.conn.execute(
            "SELECT payload_json FROM hh_pending_messages WHERE id = ?",
            (message_id,),
        ).fetchone()
        payload_json = current["payload_json"] if current and payload is None else _json_dumps_redacted(payload or {})
        self.conn.execute(
            """
            UPDATE hh_pending_messages
            SET status = ?, reason = ?, payload_json = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, reason, payload_json, utc_now(), message_id),
        )
        self.conn.commit()

    def list_hh_pending_messages(self, status: str | None = None) -> list[HHPendingMessage]:
        if status:
            rows = self.conn.execute(
                "SELECT * FROM hh_pending_messages WHERE status = ? ORDER BY id",
                (status,),
            ).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM hh_pending_messages ORDER BY id").fetchall()
        return [
            HHPendingMessage(
                id=int(row["id"]),
                action_type=row["action_type"],
                payload=json.loads(row["payload_json"]),
                confidence=float(row["confidence"]),
                status=row["status"],
                reason=row["reason"],
                ai_decision_id=None if row["ai_decision_id"] is None else int(row["ai_decision_id"]),
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]

    def append_hh_operation_log(
        self,
        *,
        operation_id: int | None,
        level: str,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO hh_operation_logs (
                operation_id, level, message, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                operation_id,
                level,
                message,
                _json_dumps_redacted(payload or {}),
                utc_now(),
            ),
        )
        self.conn.commit()
        return _required_lastrowid(cur)

    def list_hh_operation_logs(self, operation_id: int | None = None) -> list[HHOperationLog]:
        if operation_id is None:
            rows = self.conn.execute("SELECT * FROM hh_operation_logs ORDER BY id").fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM hh_operation_logs WHERE operation_id = ? ORDER BY id",
                (operation_id,),
            ).fetchall()
        return [
            HHOperationLog(
                id=int(row["id"]),
                operation_id=None if row["operation_id"] is None else int(row["operation_id"]),
                level=row["level"],
                message=row["message"],
                payload=json.loads(row["payload_json"]),
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def get_hh_agent_mcp_run(self, run_id: int) -> HHAgentMCPRun | None:
        row = self.conn.execute(
            "SELECT * FROM hh_agent_mcp_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            return None
        return HHAgentMCPRun(
            id=int(row["id"]),
            tool_name=row["tool_name"],
            input=json.loads(row["input_json"]),
            status=row["status"],
            output=json.loads(row["output_json"]),
            error=row["error"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
        )

    def upsert_hh_letter_template(self, *, name: str, body: str) -> dict[str, Any]:
        now = utc_now()
        self.conn.execute(
            """
            INSERT INTO hh_letter_templates (name, body, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                body = excluded.body,
                updated_at = excluded.updated_at
            """,
            (name, body, now, now),
        )
        self.conn.commit()
        row = self.conn.execute(
            "SELECT * FROM hh_letter_templates WHERE name = ?",
            (name,),
        ).fetchone()
        return dict(row)

    def list_hh_letter_templates(self) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM hh_letter_templates ORDER BY name").fetchall()
        return [dict(row) for row in rows]

    def delete_hh_letter_template(self, name: str) -> int:
        cur = self.conn.execute("DELETE FROM hh_letter_templates WHERE name = ?", (name,))
        self.conn.commit()
        return int(cur.rowcount)

    def upsert_hh_employer_blacklist(
        self,
        *,
        employer_id: str,
        employer_name: str = "",
        reason: str = "",
    ) -> dict[str, Any]:
        now = utc_now()
        self.conn.execute(
            """
            INSERT INTO hh_employer_blacklist (employer_id, employer_name, reason, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(employer_id) DO UPDATE SET
                employer_name = excluded.employer_name,
                reason = excluded.reason
            """,
            (employer_id, employer_name, reason, now),
        )
        self.conn.commit()
        row = self.conn.execute(
            "SELECT * FROM hh_employer_blacklist WHERE employer_id = ?",
            (employer_id,),
        ).fetchone()
        return dict(row)

    def list_hh_employer_blacklist(self) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM hh_employer_blacklist ORDER BY employer_name, employer_id").fetchall()
        return [dict(row) for row in rows]

    def delete_hh_employer_blacklist(self, employer_id: str) -> int:
        cur = self.conn.execute("DELETE FROM hh_employer_blacklist WHERE employer_id = ?", (employer_id,))
        self.conn.commit()
        return int(cur.rowcount)

    def upsert_hh_api_lab_snippet(
        self,
        *,
        name: str,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        body: Any = None,
    ) -> dict[str, Any]:
        now = utc_now()
        self.conn.execute(
            """
            INSERT INTO hh_api_lab_snippets (
                name, method, path, params_json, body_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                method = excluded.method,
                path = excluded.path,
                params_json = excluded.params_json,
                body_json = excluded.body_json,
                updated_at = excluded.updated_at
            """,
            (
                name,
                method,
                path,
                _json_dumps_redacted(params or {}),
                _json_dumps_redacted(body or {}),
                now,
                now,
            ),
        )
        self.conn.commit()
        row = self.conn.execute(
            "SELECT * FROM hh_api_lab_snippets WHERE name = ?",
            (name,),
        ).fetchone()
        return _hh_api_lab_snippet_row(row)

    def list_hh_api_lab_snippets(self) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM hh_api_lab_snippets ORDER BY name").fetchall()
        return [_hh_api_lab_snippet_row(row) for row in rows]

    def delete_hh_api_lab_snippet(self, name: str) -> int:
        cur = self.conn.execute("DELETE FROM hh_api_lab_snippets WHERE name = ?", (name,))
        self.conn.commit()
        return int(cur.rowcount)

    def save_hh_apply_from_file_state(
        self,
        *,
        source_path: str,
        row_key: str,
        vacancy_id: str,
        resume_id: str = "",
        status: str,
        result: dict[str, Any] | None = None,
    ) -> int:
        now = utc_now()
        self.conn.execute(
            """
            INSERT INTO hh_apply_from_file_state (
                source_path, row_key, vacancy_id, resume_id, status,
                result_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_path, row_key) DO UPDATE SET
                vacancy_id = excluded.vacancy_id,
                resume_id = excluded.resume_id,
                status = excluded.status,
                result_json = excluded.result_json,
                updated_at = excluded.updated_at
            """,
            (
                source_path,
                row_key,
                vacancy_id,
                resume_id,
                status,
                _json_dumps_redacted(result or {}),
                now,
                now,
            ),
        )
        self.conn.commit()
        row = self.conn.execute(
            """
            SELECT id FROM hh_apply_from_file_state
            WHERE source_path = ? AND row_key = ?
            """,
            (source_path, row_key),
        ).fetchone()
        return int(row["id"])

    def get_hh_apply_from_file_state(self, *, source_path: str, row_key: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT * FROM hh_apply_from_file_state
            WHERE source_path = ? AND row_key = ?
            """,
            (source_path, row_key),
        ).fetchone()
        return _hh_apply_from_file_state_row(row) if row else None

    def list_hh_apply_from_file_state(self, source_path: str | None = None) -> list[dict[str, Any]]:
        if source_path:
            rows = self.conn.execute(
                "SELECT * FROM hh_apply_from_file_state WHERE source_path = ? ORDER BY id",
                (source_path,),
            ).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM hh_apply_from_file_state ORDER BY id").fetchall()
        return [_hh_apply_from_file_state_row(row) for row in rows]

    def save_hh_form_review(
        self,
        *,
        vacancy_id: str = "",
        resume_id: str = "",
        status: str,
        payload: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
    ) -> int:
        now = utc_now()
        cur = self.conn.execute(
            """
            INSERT INTO hh_form_reviews (
                vacancy_id, resume_id, status, payload_json, result_json,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                vacancy_id,
                resume_id,
                status,
                _json_dumps_redacted(payload or {}),
                _json_dumps_redacted(result or {}),
                now,
                now,
            ),
        )
        self.conn.commit()
        return _required_lastrowid(cur)

    def list_hh_form_reviews(self, status: str | None = None) -> list[dict[str, Any]]:
        if status:
            rows = self.conn.execute(
                "SELECT * FROM hh_form_reviews WHERE status = ? ORDER BY id",
                (status,),
            ).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM hh_form_reviews ORDER BY id").fetchall()
        return [
            {
                "id": int(row["id"]),
                "vacancy_id": row["vacancy_id"],
                "resume_id": row["resume_id"],
                "status": row["status"],
                "payload": json.loads(row["payload_json"]),
                "result": json.loads(row["result_json"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def save_summary(self, job_id: int, summary: str) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO job_summaries (job_id, summary, created_at)
            VALUES (?, ?, ?)
            """,
            (job_id, summary, utc_now()),
        )
        self.conn.commit()

    def get_summary(self, job_id: int) -> str:
        row = self.conn.execute(
            "SELECT summary FROM job_summaries WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        return row["summary"] if row else ""

    def get_stats(self, profile_id: str = "default") -> dict[str, Any]:
        total = self.conn.execute("SELECT COUNT(*) AS cnt FROM jobs").fetchone()["cnt"]

        by_source_rows = self.conn.execute(
            "SELECT source, COUNT(*) AS cnt FROM jobs GROUP BY source"
        ).fetchall()
        by_source = {row["source"]: row["cnt"] for row in by_source_rows}

        by_status_rows = self.conn.execute(
            "SELECT status, COUNT(*) AS cnt FROM jobs GROUP BY status"
        ).fetchall()
        by_status = {row["status"]: row["cnt"] for row in by_status_rows}

        buckets = {"0-20": 0, "21-40": 0, "41-60": 0, "61-80": 0, "81-100": 0}
        score_rows = self.conn.execute(
            """
            SELECT s.total_score
            FROM job_scores s
            INNER JOIN jobs j ON s.job_id = j.id
            WHERE s.profile_id = ?
            """,
            (profile_id,),
        ).fetchall()
        for row in score_rows:
            s = row["total_score"]
            if s <= 20:
                buckets["0-20"] += 1
            elif s <= 40:
                buckets["21-40"] += 1
            elif s <= 60:
                buckets["41-60"] += 1
            elif s <= 80:
                buckets["61-80"] += 1
            else:
                buckets["81-100"] += 1

        total_applications = self.conn.execute(
            "SELECT COUNT(*) AS cnt FROM applications"
        ).fetchone()["cnt"]

        app_status_rows = self.conn.execute(
            "SELECT status, COUNT(*) AS cnt FROM applications GROUP BY status"
        ).fetchall()
        applications_by_status = {row["status"]: row["cnt"] for row in app_status_rows}

        cutoff = utc_now()
        cutoff_date = cutoff[:10]
        recent = self.conn.execute(
            "SELECT COUNT(*) AS cnt FROM jobs WHERE fetched_at >= ?",
            (cutoff_date,),
        ).fetchone()["cnt"]

        return {
            "total_jobs": total,
            "by_source": by_source,
            "by_status": by_status,
            "score_distribution": buckets,
            "total_applications": total_applications,
            "applications_by_status": applications_by_status,
            "recent_jobs": recent,
        }

    def update_job_description(self, job_id: int, description: str) -> None:
        self.conn.execute(
            "UPDATE jobs SET description = ? WHERE id = ?",
            (description, job_id),
        )
        self.conn.commit()

    def search_jobs(
        self,
        keywords: list[str],
        limit: int = 20,
        *,
        profile_id: str = "default",
    ) -> list[Job]:
        if not keywords:
            return []
        conditions = []
        params: list[Any] = []
        for kw in keywords:
            conditions.append("(j.title LIKE ? OR j.description LIKE ?)")
            params.extend([f"%{kw}%", f"%{kw}%"])
        where = "WHERE " + " AND ".join(conditions)
        rows = self.conn.execute(
            f"""
            SELECT j.*
            FROM jobs j
            LEFT JOIN job_scores s ON s.job_id = j.id AND s.profile_id = ?
            {where}
            ORDER BY COALESCE(s.total_score, -1) DESC, j.fetched_at DESC
            LIMIT ?
            """,
            (profile_id, *params, limit),
        ).fetchall()
        jobs = [self._job_from_row(row) for row in rows]
        for job in jobs:
            if job.id is not None:
                job.score = self.get_score(job.id, profile_id)
        return jobs

    def save_resume(self, resume: Resume) -> int:
        now = utc_now()
        if resume.id == 0:
            cur = self.conn.execute(
                """
                INSERT INTO resumes (name, body, profile_id, is_active, ats_score, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    resume.name,
                    resume.body,
                    resume.profile_id,
                    int(resume.is_active),
                    resume.ats_score,
                    now,
                    now,
                ),
            )
            self.conn.commit()
            return _required_lastrowid(cur)
        else:
            self.conn.execute(
                """
                UPDATE resumes SET
                    name = ?,
                    body = ?,
                    profile_id = ?,
                    is_active = ?,
                    ats_score = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    resume.name,
                    resume.body,
                    resume.profile_id,
                    int(resume.is_active),
                    resume.ats_score,
                    now,
                    resume.id,
                ),
            )
            self.conn.commit()
            return resume.id

    def get_resume(self, resume_id: int) -> Resume | None:
        row = self.conn.execute(
            "SELECT * FROM resumes WHERE id = ?",
            (resume_id,),
        ).fetchone()
        if row is None:
            return None
        return Resume(
            id=int(row["id"]),
            name=row["name"],
            body=row["body"],
            profile_id=row["profile_id"],
            is_active=bool(row["is_active"]),
            ats_score=row["ats_score"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def list_resumes(self, profile_id: str = "default") -> list[Resume]:
        rows = self.conn.execute(
            """
            SELECT * FROM resumes
            WHERE profile_id = ?
            ORDER BY is_active DESC, updated_at DESC
            """,
            (profile_id,),
        ).fetchall()
        return [
            Resume(
                id=int(row["id"]),
                name=row["name"],
                body=row["body"],
                profile_id=row["profile_id"],
                is_active=bool(row["is_active"]),
                ats_score=row["ats_score"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]

    def delete_resume(self, resume_id: int) -> None:
        self.conn.execute("DELETE FROM resumes WHERE id = ?", (resume_id,))
        self.conn.commit()

    def set_active_resume(self, resume_id: int) -> None:
        row = self.conn.execute(
            "SELECT profile_id FROM resumes WHERE id = ?",
            (resume_id,),
        ).fetchone()
        if row is None:
            return
        profile_id = row["profile_id"]
        self.conn.execute(
            "UPDATE resumes SET is_active = 0 WHERE profile_id = ?",
            (profile_id,),
        )
        self.conn.execute(
            "UPDATE resumes SET is_active = 1 WHERE id = ?",
            (resume_id,),
        )
        self.conn.commit()

    def save_event(self, event: CalendarEvent) -> int:
        now = utc_now()
        if event.id == 0:
            cur = self.conn.execute(
                """
                INSERT INTO calendar_events (job_id, title, event_type, event_date, notes, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    event.job_id,
                    event.title,
                    event.event_type,
                    event.event_date,
                    event.notes,
                    now,
                ),
            )
            self.conn.commit()
            return _required_lastrowid(cur)
        else:
            self.conn.execute(
                """
                UPDATE calendar_events SET
                    job_id = ?,
                    title = ?,
                    event_type = ?,
                    event_date = ?,
                    notes = ?
                WHERE id = ?
                """,
                (
                    event.job_id,
                    event.title,
                    event.event_type,
                    event.event_date,
                    event.notes,
                    event.id,
                ),
            )
            self.conn.commit()
            return event.id

    def list_events(self, from_date: str = "", to_date: str = "") -> list[CalendarEvent]:
        conditions: list[str] = []
        params: list[Any] = []
        if from_date:
            conditions.append("event_date >= ?")
            params.append(from_date)
        if to_date:
            conditions.append("event_date <= ?")
            params.append(to_date)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = self.conn.execute(
            f"SELECT * FROM calendar_events {where} ORDER BY event_date ASC",
            params,
        ).fetchall()
        return [
            CalendarEvent(
                id=int(row["id"]),
                job_id=row["job_id"],
                title=row["title"],
                event_type=row["event_type"],
                event_date=row["event_date"],
                notes=row["notes"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def delete_event(self, event_id: int) -> None:
        self.conn.execute("DELETE FROM calendar_events WHERE id = ?", (event_id,))
        self.conn.commit()

    def save_search(self, search: SavedSearch) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO saved_searches (id, name, query, filters_json, alert_enabled, last_checked_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                search.id if search.id != 0 else None,
                search.name,
                search.query,
                search.filters_json,
                int(search.alert_enabled),
                search.last_checked_at,
            ),
        )
        self.conn.commit()

    def list_searches(self) -> list[SavedSearch]:
        rows = self.conn.execute(
            "SELECT * FROM saved_searches ORDER BY id DESC"
        ).fetchall()
        return [
            SavedSearch(
                id=int(row["id"]),
                name=row["name"],
                query=row["query"],
                filters_json=row["filters_json"],
                alert_enabled=bool(row["alert_enabled"]),
                last_checked_at=row["last_checked_at"],
            )
            for row in rows
        ]

    def delete_search(self, search_id: int) -> None:
        self.conn.execute("DELETE FROM saved_searches WHERE id = ?", (search_id,))
        self.conn.commit()

    def update_search_checked(self, search_id: int) -> None:
        self.conn.execute(
            "UPDATE saved_searches SET last_checked_at = ? WHERE id = ?",
            (utc_now(), search_id),
        )
        self.conn.commit()

    def upsert_search_preset(
        self,
        *,
        name: str,
        source: str = "hh",
        params: dict[str, Any] | None = None,
        enabled: bool = True,
    ) -> dict[str, Any]:
        now = utc_now()
        current = self.conn.execute(
            "SELECT * FROM search_presets WHERE name = ?",
            (name,),
        ).fetchone()
        dry_run_checked_at = current["dry_run_checked_at"] if current else ""
        last_live_run_at = current["last_live_run_at"] if current else ""
        last_result_json = current["last_result_json"] if current else "{}"
        self.conn.execute(
            """
            INSERT INTO search_presets (
                name, source, params_json, dry_run_checked_at,
                last_live_run_at, last_result_json, enabled
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                source = excluded.source,
                params_json = excluded.params_json,
                enabled = excluded.enabled
            """,
            (
                name,
                source,
                _json_dumps_redacted(params or {}),
                dry_run_checked_at,
                last_live_run_at,
                last_result_json,
                int(enabled),
            ),
        )
        self.conn.commit()
        return self.get_search_preset(name) or {
            "name": name,
            "source": source,
            "params": params or {},
            "enabled": enabled,
            "created_at": now,
        }

    def get_search_preset(self, name: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM search_presets WHERE name = ?",
            (name,),
        ).fetchone()
        return _search_preset_from_row(row) if row else None

    def list_search_presets(self) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM search_presets ORDER BY name").fetchall()
        return [_search_preset_from_row(row) for row in rows]

    def update_search_preset_result(
        self,
        name: str,
        *,
        dry_run: bool,
        result: dict[str, Any],
    ) -> None:
        timestamp_column = "dry_run_checked_at" if dry_run else "last_live_run_at"
        self.conn.execute(
            f"""
            UPDATE search_presets
            SET {timestamp_column} = ?,
                last_result_json = ?
            WHERE name = ?
            """,
            (utc_now(), _json_dumps_redacted(result), name),
        )
        self.conn.commit()

    def record_event(self, job_id: int, action: str) -> None:
        self.conn.execute(
            "INSERT INTO learning_events (job_id, action, timestamp) VALUES (?, ?, ?)",
            (job_id, action, utc_now()),
        )
        self.conn.commit()

    def get_behavior_stats(self) -> dict[str, Any]:
        total_actions = self.conn.execute(
            "SELECT COUNT(*) AS cnt FROM learning_events"
        ).fetchone()["cnt"]

        by_action_rows = self.conn.execute(
            "SELECT action, COUNT(*) AS cnt FROM learning_events GROUP BY action"
        ).fetchall()
        by_action = {row["action"]: row["cnt"] for row in by_action_rows}

        most_hidden_companies_rows = self.conn.execute(
            """
            SELECT j.company, COUNT(*) AS cnt
            FROM learning_events le
            JOIN jobs j ON le.job_id = j.id
            WHERE le.action = 'hidden'
            GROUP BY j.company
            ORDER BY cnt DESC
            LIMIT 10
            """
        ).fetchall()
        most_hidden_companies = [
            {"company": row["company"], "count": row["cnt"]}
            for row in most_hidden_companies_rows
        ]

        recent_rows = self.conn.execute(
            """
            SELECT le.job_id, le.action, le.timestamp, j.title, j.company
            FROM learning_events le
            LEFT JOIN jobs j ON le.job_id = j.id
            ORDER BY le.id DESC
            LIMIT 20
            """
        ).fetchall()
        recent_actions = [
            {
                "job_id": row["job_id"],
                "action": row["action"],
                "timestamp": row["timestamp"],
                "title": row["title"] or "",
                "company": row["company"] or "",
            }
            for row in recent_rows
        ]

        return {
            "total_actions": total_actions,
            "by_action": by_action,
            "most_hidden_companies": most_hidden_companies,
            "recent_actions": recent_actions,
        }

    def get_ghost_jobs(
        self,
        days: int = 7,
        *,
        profile_id: str = "default",
    ) -> list[Job]:
        cutoff = _utc_cutoff_days(days)
        rows = self.conn.execute(
            """
            SELECT j.*
            FROM jobs j
            INNER JOIN applications a ON a.job_id = j.id
            WHERE j.status = 'applied'
              AND a.status = 'applied'
              AND julianday(a.applied_at) <= julianday(?)
            ORDER BY julianday(a.applied_at) ASC, a.id ASC
            """,
            (cutoff,),
        ).fetchall()
        jobs = [self._job_from_row(row) for row in rows]
        for job in jobs:
            if job.id is not None:
                job.score = self.get_score(job.id, profile_id)
        return jobs

    def _job_from_row(self, row: sqlite3.Row) -> Job:
        remote_raw = row["remote"]
        return Job(
            id=int(row["id"]),
            source=row["source"],
            source_id=row["source_id"],
            url=row["url"],
            title=row["title"],
            company=row["company"],
            salary_text=row["salary_text"],
            salary_from=row["salary_from"],
            salary_to=row["salary_to"],
            currency=row["currency"],
            location=row["location"],
            remote=None if remote_raw is None else bool(remote_raw),
            description=row["description"],
            published_at=row["published_at"],
            fetched_at=row["fetched_at"],
            status=row["status"],
        )
