from __future__ import annotations

import html
import json
import secrets
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class TelegramReply:
    text: str
    parse_mode: str = "HTML"
    reply_markup: list[list[dict[str, str]]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"text": self.text, "parse_mode": self.parse_mode}
        if self.reply_markup:
            payload["reply_markup"] = {"inline_keyboard": self.reply_markup}
        return payload


class CockpitAPIClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def get(self, path: str) -> Any:
        return self._request("GET", path)

    def post(self, path: str, payload: dict | None = None) -> Any:
        return self._request("POST", path, payload or {})

    def _request(self, method: str, path: str, payload: dict | None = None) -> Any:
        url = f"{self.base_url}{path if path.startswith('/') else '/' + path}"
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(request, timeout=10) as response:
            raw = response.read().decode("utf-8")
        return json.loads(raw or "{}")


class TelegramRemote:
    def __init__(
        self,
        *,
        config: dict[str, Any],
        api: Any | None = None,
        state_factory: Callable[[], str] | None = None,
    ):
        self.config = config
        self.allowed_user_ids = {int(item) for item in config.get("allowed_user_ids") or []}
        self.cockpit_base_url = str(config.get("cockpit_base_url") or "http://127.0.0.1:8787").rstrip("/")
        self.oauth_start_path = str(config.get("oauth_start_path") or "/hh/oauth/start")
        self.api = api or CockpitAPIClient(self.cockpit_base_url)
        self.state_factory = state_factory or (lambda: secrets.token_urlsafe(24))

    def handle_update(self, update: dict[str, Any]) -> TelegramReply:
        callback = update.get("callback_query")
        if isinstance(callback, dict):
            user_id = _telegram_user_id(callback)
            if not self._is_allowed(user_id):
                return TelegramReply("Access denied.")
            return self._handle_callback(str(callback.get("data") or ""))
        message = update.get("message") or {}
        user_id = _telegram_user_id(message)
        if not self._is_allowed(user_id):
            return TelegramReply("Access denied.")
        return self._handle_command(str(message.get("text") or "").strip())

    def _is_allowed(self, user_id: int | None) -> bool:
        return bool(user_id is not None and user_id in self.allowed_user_ids)

    def _handle_command(self, text: str) -> TelegramReply:
        if not text:
            return self._help()
        command, _, rest = text.partition(" ")
        command = command.split("@", 1)[0].lower()
        rest = rest.strip()
        if command in {"/start", "/help"}:
            return self._help()
        if command == "/status":
            return self._status()
        if command == "/digest":
            return self._digest()
        if command == "/pending":
            return self._pending()
        if command == "/next":
            return self._next()
        if command == "/reply":
            return self._reply(rest)
        if command == "/pause":
            result = self.api.post("/api/agent/pause", {"reason": "telegram"})
            return TelegramReply(f"Agent paused: {_e(str(result.get('status') or 'ok'))}")
        if command == "/resume":
            result = self.api.post("/api/agent/resume", {"reason": "telegram"})
            return TelegramReply(f"Agent resumed: {_e(str(result.get('status') or 'ok'))}")
        if command == "/events":
            return self._events()
        if command == "/runs":
            return self._runs()
        if command == "/skipped":
            return self._skipped()
        if command in {"/oauth", "/auth"}:
            return self._oauth()
        return TelegramReply("Unknown command.\n" + self._help().text)

    def _status(self) -> TelegramReply:
        data = self.api.get("/api/agent/preflight")
        counts = data.get("counts") or {}
        lines = [
            f"<b>HH Agent</b>: {_e(str(data.get('status') or 'unknown'))}",
            f"auth: {_e(str((data.get('auth') or {}).get('authorized')))}",
            f"pending: {_e(str(counts.get('pending_approvals', 0)))}",
            f"events/tasks: {_e(str(counts.get('agent_events', 0)))}/{_e(str(counts.get('agent_tasks', 0)))}",
            f"outbox: {_e(str(counts.get('outbox', 0)))}",
        ]
        return TelegramReply("\n".join(lines))

    def _digest(self) -> TelegramReply:
        data = self.api.get("/api/agent/digest?limit=5")
        summary = data.get("summary") or {}
        approvals = data.get("approvals") or {}
        lines = [
            "<b>Digest</b>",
            f"resumes: {_e(str((summary.get('resumes') or {}).get('total', 0)))}",
            f"skipped: {_e(str((summary.get('skipped') or {}).get('total', 0)))}",
            f"pending: {_e(str((approvals.get('by_status') or {}).get('pending', 0)))}",
            f"runs: {_e(str((data.get('runs') or {}).get('total', 0)))}",
            f"outbox: {_e(str((data.get('outbox') or {}).get('total', 0)))}",
            f"webhooks: {_e(str((data.get('webhooks') or {}).get('total', 0)))}",
        ]
        return TelegramReply("\n".join(lines))

    def _pending(self) -> TelegramReply:
        items = self.api.get("/api/approvals?status=pending")
        if not items:
            return TelegramReply("No pending approvals.")
        lines = ["<b>Pending approvals</b>"]
        markup: list[list[dict[str, str]]] = []
        for item in list(items)[:5]:
            item_id = int(item.get("id") or 0)
            preview = _approval_preview(item)
            lines.append(
                f"#{item_id} {_e(str(item.get('action_type') or 'approval'))}: "
                f"{_e(str(item.get('reason') or ''))}\n{_e(preview)}"
            )
            markup.append(
                [
                    {"text": "Approve", "callback_data": f"approve:{item_id}"},
                    {"text": "Reject", "callback_data": f"reject:{item_id}"},
                    {"text": "Flag", "callback_data": f"flag:{item_id}"},
                ]
            )
        return TelegramReply("\n\n".join(lines), reply_markup=markup)

    def _next(self) -> TelegramReply:
        data = self.api.get("/api/agent/digest?limit=5")
        approvals = ((data.get("approvals") or {}).get("recent") or [])
        if approvals:
            item = approvals[0]
            return TelegramReply(
                f"Next: {_e(str(item.get('action_type') or 'approval'))} "
                f"#{_e(str(item.get('id') or ''))} - {_e(str(item.get('reason') or ''))}"
            )
        agenda = data.get("agenda") or {}
        tasks = (agenda.get("tasks") or {}).get("recent") or []
        if tasks:
            item = tasks[0]
            return TelegramReply(f"Next task: {_e(str(item.get('title') or 'task'))}")
        events = (agenda.get("events") or {}).get("recent") or []
        if events:
            item = events[0]
            return TelegramReply(f"Next event: {_e(str(item.get('title') or 'event'))}")
        return TelegramReply("Nothing urgent.")

    def _reply(self, rest: str) -> TelegramReply:
        message_id, _, body = rest.partition(" ")
        if not message_id.isdigit() or not body.strip():
            return TelegramReply("Usage: /reply <approval_id> <message>")
        self.api.post(
            f"/api/approvals/{int(message_id)}/modify",
            {"instruction": "telegram_reply", "payload_patch": {"message": body.strip()}},
        )
        return TelegramReply(f"Reply updated #{int(message_id)}.")

    def _events(self) -> TelegramReply:
        items = self.api.get("/api/agent/events?limit=5")
        if not items:
            return TelegramReply("No agent events.")
        lines = ["<b>Events</b>"]
        for item in list(items)[:5]:
            lines.append(f"{_e(str(item.get('event_at') or ''))} - {_e(str(item.get('title') or 'event'))}")
        return TelegramReply("\n".join(lines))

    def _runs(self) -> TelegramReply:
        data = self.api.get("/api/operations?limit=5")
        runs = data.get("runs") or []
        if not runs:
            return TelegramReply("No runs.")
        lines = ["<b>Runs</b>"]
        for run in runs[:5]:
            lines.append(f"#{_e(str(run.get('id') or ''))} {_e(str(run.get('tool_name') or 'run'))}: {_e(str(run.get('status') or ''))}")
        return TelegramReply("\n".join(lines))

    def _skipped(self) -> TelegramReply:
        items = self.api.get("/api/hh/skipped")
        if not items:
            return TelegramReply("No skipped HH vacancies.")
        lines = ["<b>Skipped</b>"]
        for item in list(items)[:5]:
            title = item.get("name") or item.get("vacancy_id") or "vacancy"
            lines.append(f"{_e(str(title))}: {_e(str(item.get('reason') or ''))}")
        return TelegramReply("\n".join(lines))

    def _oauth(self) -> TelegramReply:
        state = self.state_factory()
        query = urllib.parse.urlencode({"state": state})
        link = f"{self.cockpit_base_url}{self.oauth_start_path}?{query}"
        return TelegramReply(f"Open OAuth onboarding:\n{_e(link)}")

    def _handle_callback(self, data: str) -> TelegramReply:
        action, _, rest = data.partition(":")
        if action in {"approve", "reject", "flag", "sanity"}:
            message_id = _first_int(rest)
            if message_id is None:
                return TelegramReply("Invalid callback.")
            if action == "approve":
                self.api.post(f"/api/approvals/{message_id}/approve", {"reason": "telegram_approve"})
                return TelegramReply(f"approved #{message_id}")
            if action == "reject":
                self.api.post(f"/api/approvals/{message_id}/reject", {"reason": "telegram_reject"})
                return TelegramReply(f"rejected #{message_id}")
            if action == "flag":
                self.api.post(f"/api/approvals/{message_id}/flag", {"reason": "telegram_flag"})
                return TelegramReply(f"flagged #{message_id}")
            self.api.post(f"/api/approvals/{message_id}/flag", {"reason": "sanity_check_requested"})
            return TelegramReply(f"sanity requested #{message_id}")
        if action == "modify":
            message_id_text, _, instruction = rest.partition(":")
            if not message_id_text.isdigit() or not instruction:
                return TelegramReply("Invalid modify callback.")
            message_id = int(message_id_text)
            self.api.post(
                f"/api/approvals/{message_id}/modify",
                {"instruction": instruction, "payload_patch": {}},
            )
            return TelegramReply(f"modified #{message_id}")
        return TelegramReply("Unknown callback.")

    def _help(self) -> TelegramReply:
        return TelegramReply(
            "Commands: /status /digest /pending /next /reply /pause /resume /events /runs /skipped /oauth"
        )


def _telegram_user_id(container: dict[str, Any]) -> int | None:
    user = container.get("from") or {}
    value = user.get("id") if isinstance(user, dict) else None
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _approval_preview(item: dict[str, Any]) -> str:
    payload = item.get("payload") or {}
    if isinstance(payload.get("reply"), dict):
        return str(payload["reply"].get("message") or "")
    return str(payload.get("message") or payload.get("letter") or "")


def _first_int(value: str) -> int | None:
    raw = value.split(":", 1)[0]
    return int(raw) if raw.isdigit() else None


def _e(value: str) -> str:
    return html.escape(value, quote=False)
