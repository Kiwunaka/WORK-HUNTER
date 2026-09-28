"""Read-only application cohort analytics; search results are not sends."""
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

MSK = timezone(timedelta(hours=3))
SENT = {"applied", "sent", "submitted", "manually_applied", "manual", "replied",
        "response", "responded", "phone_screen", "interview", "interview_scheduled", "offer", "rejected"}
PENDING = {"submitting", "submission_unknown", "submitted_unconfirmed", "reconciling"}


def application_analytics(storage: Any, days: int = 30, *, now: datetime | None = None) -> dict:
    """One vacancy per account, irrespective of the number of selected resumes.

    Outcomes reflect the latest application record, not inferred employer replies.
    In particular, HH negotiation state 'response' is NOT an employer reply.
    """
    today = (now or datetime.now(MSK)).astimezone(MSK).date()
    start = today - timedelta(days=days - 1)
    rows = storage.conn.execute("""
        SELECT a.*, COALESCE(NULLIF(a.source, ''), j.source) AS board,
               COALESCE(NULLIF(a.source_id, ''), j.source_id) AS vacancy
        FROM applications a JOIN jobs j ON j.id = a.job_id
        ORDER BY a.updated_at, a.id
    """).fetchall()
    groups: dict[tuple, list] = {}
    for row in rows:
        key = (row["account_profile_id"], row["board"], row["vacancy"] or row["job_id"])
        groups.setdefault(key, []).append(row)
    source_counts: dict[str, Counter] = {}
    pending_keys: set[tuple] = set()
    confirmed_keys: set[tuple] = set()
    daily = {str(start + timedelta(days=i)): 0 for i in range(days)}
    undated = 0
    for key, versions in groups.items():
        confirmed = [r for r in versions if r["status"] in SENT]
        source = versions[-1]["board"]
        counts = source_counts.setdefault(source, Counter())
        if not confirmed:
            if versions[-1]["status"] in PENDING:
                pending_keys.add(key)  # Backlog is all-time, labelled separately.
            continue
        confirmed_keys.add(key)
        dates = []
        for row in confirmed:
            try:
                stamp = datetime.fromisoformat(row["sent_at"] or row["applied_at"])
                dates.append(stamp.replace(tzinfo=stamp.tzinfo or timezone.utc).astimezone(MSK).date())
            except (TypeError, ValueError):
                pass
        if not dates:
            undated += 1
            continue
        sent_day = min(dates)
        if not start <= sent_day <= today:
            continue
        counts["sent"] += 1
        daily[str(sent_day)] += 1
        status = confirmed[-1]["status"]
        if status in {"response", "responded", "replied", "phone_screen"}:
            counts["reply"] += 1
        elif status in {"interview", "interview_scheduled"}:
            counts["interview"] += 1
        elif status in {"offer", "rejected"}:
            counts[status] += 1
    # Autopilot writes uncertain sends to the attempt ledger before an
    # application exists. Include its latest attempt, deduplicated with above.
    attempts = storage.conn.execute("""
        SELECT a.account_profile_id, a.vacancy_id, a.status
        FROM hh_application_attempts a
        JOIN (SELECT MAX(id) AS id FROM hh_application_attempts
              GROUP BY account_profile_id, vacancy_id) latest ON latest.id=a.id
        WHERE a.status IN ('reconciling', 'submitting', 'submission_unknown', 'submitted_unconfirmed')
    """).fetchall()
    pending_keys.update((r["account_profile_id"], "hh", r["vacancy_id"]) for r in attempts)
    for key in pending_keys - confirmed_keys:
        source_counts.setdefault(key[1], Counter())["pending"] += 1
    sources: list[dict[str, Any]] = [{"source": source, **{key: counts[key] for key in
                ("sent", "reply", "interview", "offer", "rejected", "pending")}}
               for source, counts in source_counts.items() if counts]
    sources.sort(key=lambda row: (-row["sent"], row["source"]))
    return {
        "days": days, "from": str(start), "to": str(today), "timezone": "Europe/Moscow",
        "sent": sum(row["sent"] for row in sources),
        "pending": sum(row["pending"] for row in sources), "undated": undated,
        "sources": sources, "daily": [{"date": day, "count": count} for day, count in daily.items()],
        "note": "Все аккаунты; одна вакансия на аккаунт независимо от резюме. Период — по дате отправки (МСК). "
                "Ответы, интервью, офферы и отказы — текущие статусы журнала, не полная история переписки. "
                "Ноль означает отсутствие записи, а не доказанное отсутствие ответа. Неподтверждённые — за всё время.",
    }
