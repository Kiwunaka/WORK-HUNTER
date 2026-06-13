from __future__ import annotations

import hashlib
import hmac
import json

from work_hunter.hh_agent.webhook import WebhookClient, WebhookSigner
from work_hunter.storage import Storage


def test_webhook_signer_builds_hmac_headers():
    signer = WebhookSigner(secret="secret")
    payload = {"reply_id": 1, "status": "planned"}

    headers = signer.headers(
        event_type="agent_reply_planned",
        payload=payload,
        timestamp="2026-06-10T10:00:00Z",
    )
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    expected = hmac.new(
        b"secret",
        b"2026-06-10T10:00:00Z.agent_reply_planned." + body.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    assert headers["X-Work-Hunter-Event"] == "agent_reply_planned"
    assert headers["X-Work-Hunter-Timestamp"] == "2026-06-10T10:00:00Z"
    assert headers["X-Work-Hunter-Signature"] == f"sha256={expected}"


def test_webhook_client_stores_failure_and_retries_pending_delivery(tmp_path):
    storage = Storage(tmp_path / "db.sqlite3")
    calls: list[dict] = []

    def fake_transport(url: str, *, body: bytes, headers: dict[str, str]):
        calls.append({"url": url, "body": body.decode("utf-8"), "headers": headers})
        if len(calls) == 1:
            raise RuntimeError("temporary down")
        return {"status": 200}

    client = WebhookClient(
        storage=storage,
        url="https://hooks.example.test/hh",
        secret="secret",
        transport=fake_transport,
    )

    failed = client.send_event("agent_reply_planned", {"reply_id": 1})
    retried = client.retry_pending()
    rows = storage.list_hh_agent_webhooks()

    assert failed["status"] == "error"
    assert retried == {"status": "ok", "sent": 1, "errors": 0}
    assert len(calls) == 2
    assert rows[0].status == "sent"
    assert rows[0].attempts == 2
    assert rows[0].event_type == "agent_reply_planned"
    assert rows[0].payload["reply_id"] == 1
    assert calls[0]["headers"]["X-Work-Hunter-Signature"].startswith("sha256=")
