from __future__ import annotations

from work_hunter.models import HHEmployer
from work_hunter.services import WorkHunter


def test_enrich_hh_employers_stores_site_snapshot_and_public_emails(monkeypatch, tmp_path):
    app = WorkHunter(root=tmp_path)
    app.storage.upsert_hh_employer(
        HHEmployer(id="emp-1", name="Acme", site_url="https://acme.test")
    )

    def fake_fetch_url(url: str, **kwargs):
        assert url == "https://acme.test"
        return """
        <html>
          <body>
            Careers: <a href="mailto:jobs@acme.test">jobs</a>
            Contact HR at hr@acme.test.
          </body>
        </html>
        """

    monkeypatch.setattr("work_hunter.services.fetch_url", fake_fetch_url)

    result = app.enrich_hh_employers(limit=10)

    assert result["status"] == "ok"
    assert result["count"] == 1
    assert result["employers"][0]["emails"] == ["jobs@acme.test", "hr@acme.test"]
    snapshots = app.storage.list_hh_employer_snapshots()
    assert len(snapshots) == 1
    assert snapshots[0]["employer_id"] == "emp-1"
    assert snapshots[0]["status"] == "ok"
    assert snapshots[0]["emails"] == ["jobs@acme.test", "hr@acme.test"]
    assert "Careers" in snapshots[0]["text"]


def test_plan_hh_email_followups_uses_snapshot_email_and_template(tmp_path):
    app = WorkHunter(root=tmp_path)
    app.storage.upsert_hh_employer(
        HHEmployer(id="emp-1", name="Acme", site_url="https://acme.test")
    )
    app.storage.save_hh_employer_snapshot(
        employer_id="emp-1",
        site_url="https://acme.test",
        html="<p>hr@acme.test</p>",
        text="Contact hr@acme.test",
        emails=["hr@acme.test"],
        status="ok",
    )

    plan = app.plan_hh_email_followups(
        template="Здравствуйте, {employer_name}! Пишу по поводу возможностей в {site_url}.",
        subject="Backend engineer",
    )

    assert plan["status"] == "planned"
    assert plan["count"] == 1
    assert plan["followups"][0]["to"] == "hr@acme.test"
    assert plan["followups"][0]["subject"] == "Backend engineer"
    assert "Acme" in plan["followups"][0]["body"]


def test_send_hh_email_followups_requires_confirm_and_sends_with_injected_sender(tmp_path):
    app = WorkHunter(root=tmp_path)
    app.storage.upsert_hh_employer(
        HHEmployer(id="emp-1", name="Acme", site_url="https://acme.test")
    )
    app.storage.save_hh_employer_snapshot(
        employer_id="emp-1",
        site_url="https://acme.test",
        html="",
        text="",
        emails=["hr@acme.test"],
        status="ok",
    )
    sent: list[dict] = []

    blocked = app.send_hh_email_followups(template="Hi {employer_name}", confirm=False)
    result = app.send_hh_email_followups(
        template="Hi {employer_name}",
        subject="Follow-up",
        confirm=True,
        sender=lambda message: sent.append(message) or {"status": "sent"},
    )

    assert blocked["status"] == "blocked"
    assert result["status"] == "sent"
    assert result["count"] == 1
    assert sent == [
        {
            "employer_id": "emp-1",
            "employer_name": "Acme",
            "to": "hr@acme.test",
            "subject": "Follow-up",
            "body": "Hi Acme",
        }
    ]
    events = app.storage.list_hh_email_followups()
    assert events[0]["status"] == "sent"
    assert events[0]["to_email"] == "hr@acme.test"
