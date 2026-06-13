from __future__ import annotations

import hashlib
import hmac
import json
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable


WebhookTransport = Callable[[str], Any]


class WebhookSigner:
    def __init__(self, *, secret: str):
        self.secret = secret

    def headers(
        self,
        *,
        event_type: str,
        payload: dict[str, Any],
        timestamp: str | None = None,
    ) -> dict[str, str]:
        ts = timestamp or _utc_timestamp()
        body = _canonical_json(payload)
        message = f"{ts}.{event_type}.{body}".encode("utf-8")
        signature = hmac.new(self.secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
        return {
            "Content-Type": "application/json; charset=utf-8",
            "X-Work-Hunter-Event": event_type,
            "X-Work-Hunter-Timestamp": ts,
            "X-Work-Hunter-Signature": f"sha256={signature}",
        }


class WebhookClient:
    def __init__(
        self,
        *,
        storage: Any,
        url: str,
        secret: str,
        transport: Callable[..., Any] | None = None,
    ):
        if not url:
            raise ValueError("Webhook url is required")
        self.storage = storage
        self.url = url
        self.signer = WebhookSigner(secret=secret)
        self.transport = transport or _urllib_transport

    def send_event(self, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        delivery_id = self.storage.create_hh_agent_webhook(
            event_type=event_type,
            payload=payload,
            status="pending",
        )
        return self._deliver(delivery_id, event_type=event_type, payload=payload, attempts=0)

    def retry_pending(self, *, max_attempts: int = 3) -> dict[str, Any]:
        sent = 0
        errors = 0
        for delivery in self.storage.list_hh_agent_webhooks():
            if delivery.status == "sent" or delivery.attempts >= max_attempts:
                continue
            result = self._deliver(
                delivery.id,
                event_type=delivery.event_type,
                payload=delivery.payload,
                attempts=delivery.attempts,
            )
            if result["status"] == "sent":
                sent += 1
            else:
                errors += 1
        return {"status": "ok", "sent": sent, "errors": errors}

    def _deliver(
        self,
        delivery_id: int,
        *,
        event_type: str,
        payload: dict[str, Any],
        attempts: int,
    ) -> dict[str, Any]:
        next_attempt = attempts + 1
        body = _canonical_json(payload).encode("utf-8")
        headers = self.signer.headers(event_type=event_type, payload=payload)
        try:
            response = self.transport(self.url, body=body, headers=headers)
            self.storage.update_hh_agent_webhook(
                delivery_id,
                status="sent",
                attempts=next_attempt,
                last_error="",
            )
            return {"status": "sent", "delivery_id": delivery_id, "attempts": next_attempt, "response": response}
        except Exception as exc:
            self.storage.update_hh_agent_webhook(
                delivery_id,
                status="error",
                attempts=next_attempt,
                last_error=str(exc),
            )
            return {"status": "error", "delivery_id": delivery_id, "attempts": next_attempt, "error": str(exc)}


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _urllib_transport(url: str, *, body: bytes, headers: dict[str, str]) -> dict[str, Any]:
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=10) as response:
        return {"status": response.status, "body": response.read().decode("utf-8", errors="replace")}
