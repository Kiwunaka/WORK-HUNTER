from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlsplit, urlunsplit

from .repository import ChallengeRecord, ItemRecord, LeaseRecord, LostLease, StaleWrite
from .types import AutopilotState, DispatchOutcome


FIELD_SOURCES: dict[str, tuple[str, str]] = {
    "first_name": ("candidate", "first_name"),
    "last_name": ("candidate", "last_name"),
    "full_name": ("candidate", "full_name"),
    "city": ("candidate", "city"),
    "phone": ("candidate", "phone"),
    "email": ("candidate", "email"),
    "citizenship": ("candidate", "citizenships"),
    "work_permit": ("candidate", "work_permits"),
    "salary": ("candidate", "salary_min"),
    "cover_letter": ("prepared", "cover_letter"),
}

_FIELD_ALIASES = {
    "name": "first_name",
    "имя": "first_name",
    "город": "city",
    "location": "city",
    "телефон": "phone",
    "telephone": "phone",
    "e_mail": "email",
    "mail": "email",
    "comment": "cover_letter",
    "message": "cover_letter",
    "комментарий": "cover_letter",
    "сопроводительное_письмо": "cover_letter",
}
_KNOWLEDGE_KINDS = {"knowledge", "assessment", "test", "task", "quiz", "file"}
_SUPPORTED_KINDS = {
    "text",
    "textarea",
    "number",
    "email",
    "tel",
    "phone",
    "select",
    "radio",
    "checkbox",
    "boolean",
}


@dataclass(frozen=True)
class MappingResult:
    answers: dict[str, Any] = field(default_factory=dict)
    unknown_required: tuple[str, ...] = ()
    outcome: str = "mapped"
    source_labels: dict[str, str] = field(default_factory=dict)

    @property
    def complete(self) -> bool:
        return self.outcome == "mapped" and not self.unknown_required


@dataclass(frozen=True)
class ChallengeResult:
    outcome: str
    item_id: int | None = None
    challenge_id: int | None = None
    state: AutopilotState | None = None
    mapping: MappingResult | None = None


class GroundedAnswerMapper:
    """Authorize automatic form answers from a small explicit-fact registry."""

    def map(
        self,
        fields: Sequence[Any],
        candidate: Mapping[str, Any],
        resume: Mapping[str, Any],
        prepared: Mapping[str, Any] | None = None,
    ) -> MappingResult:
        sources: dict[str, Mapping[str, Any]] = {
            "candidate": candidate,
            "resume": resume,
            "prepared": prepared or {},
        }
        answers: dict[str, Any] = {}
        labels: dict[str, str] = {}
        unknown: list[str] = []
        assessment = False

        for raw in fields:
            field_value = _field_mapping(raw)
            name = str(
                field_value.get("name")
                or field_value.get("id")
                or field_value.get("key")
                or ""
            ).strip()
            if not name:
                continue
            semantic_id = normalize_field_id(field_value)
            kind = str(
                field_value.get("type") or field_value.get("input_type") or "text"
            ).strip().casefold()
            required = bool(field_value.get("required", False))
            if kind in _KNOWLEDGE_KINDS or kind not in _SUPPORTED_KINDS:
                assessment = assessment or required
                continue

            source_path = FIELD_SOURCES.get(semantic_id)
            value: Any = None
            source_name = ""
            if source_path is not None:
                source_name, key = source_path
                value = sources[source_name].get(key)
            else:
                # Exact keys are explicit profile facts. Labels and resume prose are
                # never searched because that would turn display text into guesses.
                for candidate_source in ("candidate", "prepared", "resume"):
                    if semantic_id in sources[candidate_source]:
                        source_name = candidate_source
                        value = sources[candidate_source][semantic_id]
                        break

            value = _validated_value(field_value, value)
            if _missing(value):
                if required:
                    unknown.append(semantic_id)
                continue
            answers[name] = value
            labels[name] = source_name

        if assessment:
            return MappingResult(
                answers=answers,
                unknown_required=tuple(dict.fromkeys(unknown)),
                outcome="manual_assessment",
                source_labels=labels,
            )
        if unknown:
            return MappingResult(
                answers=answers,
                unknown_required=tuple(dict.fromkeys(unknown)),
                outcome="missing_required_data",
                source_labels=labels,
            )
        return MappingResult(answers=answers, source_labels=labels)


class HHChallengeHandler:
    def __init__(
        self,
        repository: Any | None = None,
        *,
        browser: Any | None = None,
        browser_session: Any | None = None,
        mapper: GroundedAnswerMapper | None = None,
        challenge_expiry_hours: int = 24,
        lease_ttl_provider: Callable[[], int] | None = None,
        owner_token_factory: Callable[[], str] | None = None,
    ) -> None:
        self.repository = repository
        self.browser = browser
        self.browser_session = browser_session
        self.mapper = mapper or GroundedAnswerMapper()
        self.challenge_expiry_hours = int(challenge_expiry_hours)
        self.lease_ttl_provider = lease_ttl_provider or (lambda: 120)
        self.owner_token_factory = owner_token_factory or (
            lambda: f"hh-challenge-expiry:{uuid.uuid4().hex}"
        )
        if self.challenge_expiry_hours < 1:
            raise ValueError("challenge_expiry_hours must be positive")
        if not callable(self.lease_ttl_provider):
            raise TypeError("lease_ttl_provider must be callable")
        if not callable(self.owner_token_factory):
            raise TypeError("owner_token_factory must be callable")

    def expire_due(
        self,
        now: datetime,
    ) -> tuple[dict[str, Any], ...]:
        if self.repository is None:
            return ()
        instant = _aware_utc(now)
        due = self.repository.due_challenges(now=instant)
        by_account: dict[str, list[ChallengeRecord]] = {}
        for challenge in due:
            by_account.setdefault(challenge.account_id, []).append(challenge)

        updates: list[dict[str, Any]] = []
        for account_id, challenges in by_account.items():
            ttl_seconds = int(self.lease_ttl_provider())
            if ttl_seconds < 1:
                raise ValueError("challenge expiry lease TTL must be positive")
            lease = self.repository.acquire_lease(
                account_id,
                self.owner_token_factory(),
                ttl_seconds=ttl_seconds,
                now=instant,
            )
            if lease is None:
                updates.extend(
                    {
                        "challenge_id": challenge.id,
                        "account_id": account_id,
                        "status": "busy",
                    }
                    for challenge in challenges
                )
                continue
            try:
                for challenge in challenges:
                    try:
                        if challenge.challenge_type == "ambiguous_application":
                            item = self.repository.expire_challenge(
                                challenge.id,
                                fencing_token=lease.fencing_token,
                                now=instant,
                            )
                        else:
                            item = self.repository.resolve_manual_challenge(
                                challenge.id,
                                action="expire",
                                actor="scheduler",
                                fencing_token=lease.fencing_token,
                                now=instant,
                            )
                        updates.append(
                            {
                                "challenge_id": challenge.id,
                                "account_id": account_id,
                                "item_id": item.id,
                                "state": item.state.value,
                                "status": "expired",
                            }
                        )
                    except (KeyError, LostLease, StaleWrite, ValueError):
                        updates.append(
                            {
                                "challenge_id": challenge.id,
                                "account_id": account_id,
                                "status": "stale",
                            }
                        )
            finally:
                self.repository.release_lease(lease)
        return tuple(updates)

    def handle_required_flow(
        self,
        *,
        kind: str,
        mode: str,
        fields: Sequence[Any] = (),
        candidate: Mapping[str, Any] | None = None,
        resume: Mapping[str, Any] | None = None,
        prepared: Mapping[str, Any] | None = None,
    ) -> ChallengeResult:
        kind = str(kind).strip().casefold()
        mode = str(mode).strip().casefold()
        if kind not in {"screening", "form"}:
            raise ValueError("kind must be screening or form")
        if mode == "off":
            return ChallengeResult(outcome=f"{kind}_disabled")
        mapping = self.mapper.map(
            fields,
            candidate or {},
            resume or {},
            prepared=prepared,
        )
        return ChallengeResult(outcome=mapping.outcome, mapping=mapping)

    def handle(
        self,
        outcome: DispatchOutcome | str,
        item: ItemRecord,
        lease: LeaseRecord,
    ) -> ChallengeResult:
        code = outcome.code if isinstance(outcome, DispatchOutcome) else str(outcome)
        location = outcome.location if isinstance(outcome, DispatchOutcome) else ""
        if code == "manual_captcha":
            return self.handle_captcha(item, url=location, lease=lease)
        challenge_type = {
            "manual_assessment": "manual_assessment",
            "form_required": "manual_assessment",
            "auth_expired": "manual_auth",
            "manual_auth": "manual_auth",
        }.get(code)
        if challenge_type is None:
            return ChallengeResult(code, item.id, state=item.state)
        return self._open(challenge_type, item, lease, location)

    def handle_captcha(
        self,
        item: ItemRecord,
        *,
        url: str,
        lease: LeaseRecord,
    ) -> ChallengeResult:
        return self._open("manual_captcha", item, lease, url)

    def _open(
        self,
        challenge_type: str,
        item: ItemRecord,
        lease: LeaseRecord,
        url: str,
    ) -> ChallengeResult:
        if self.repository is None:
            raise RuntimeError("challenge repository is required")
        challenge: ChallengeRecord = self.repository.open_manual_challenge(
            item.id,
            expected_version=item.version,
            challenge_type=challenge_type,
            sanitized_url=sanitize_hh_url(url),
            fencing_token=lease.fencing_token,
            expiry_hours=self.challenge_expiry_hours,
        )
        current = self.repository.get_item(item.id)
        return ChallengeResult(
            challenge_type,
            item.id,
            challenge.id,
            None if current is None else current.state,
        )

    def resolve(
        self,
        challenge_id: int,
        *,
        action: str,
        actor: str,
        lease: LeaseRecord | None = None,
        now: datetime | str | None = None,
    ) -> ChallengeResult:
        if self.repository is None:
            raise RuntimeError("challenge repository is required")
        challenge = self.repository.get_challenge(challenge_id)
        if challenge is None:
            raise KeyError(f"challenge {challenge_id} does not exist")
        active_lease = lease or self.repository.get_lease(challenge.account_id)
        if active_lease is None:
            raise RuntimeError("an active account lease is required")
        item = self.repository.resolve_manual_challenge(
            challenge_id,
            action=action,
            actor=actor,
            fencing_token=active_lease.fencing_token,
            now=now,
        )
        return ChallengeResult(action, item.id, challenge_id, item.state)


def normalize_field_id(field_value: Mapping[str, Any]) -> str:
    raw = str(
        field_value.get("semantic_id")
        or field_value.get("name")
        or field_value.get("id")
        or field_value.get("key")
        or field_value.get("label")
        or ""
    ).strip()
    normalized = re.sub(r"[^\w]+", "_", raw.casefold(), flags=re.UNICODE).strip("_")
    return _FIELD_ALIASES.get(normalized, normalized)


def sanitize_hh_url(url: str) -> str:
    value = str(url or "").strip()
    if not value:
        return ""
    try:
        parts = urlsplit(value)
        port = f":{parts.port}" if parts.port is not None else ""
    except ValueError:
        return ""
    if parts.scheme.casefold() not in {"http", "https"} or not parts.hostname:
        return ""
    host = parts.hostname.casefold().rstrip(".")
    return urlunsplit((parts.scheme.casefold(), host + port, parts.path or "/", "", ""))


def _aware_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError("challenge expiry time must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("challenge expiry time must be timezone-aware")
    return value.astimezone(timezone.utc)


def _field_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    fields = getattr(value, "__dict__", None)
    if isinstance(fields, Mapping):
        normalized = dict(fields)
        if "type" not in normalized and "kind" in normalized:
            normalized["type"] = normalized["kind"]
        return normalized
    raise TypeError("form fields must be mappings or dataclass-like objects")


def _validated_value(field_value: Mapping[str, Any], value: Any) -> Any:
    if _missing(value):
        return None
    kind = str(field_value.get("type") or "text").casefold()
    options = field_value.get("options") or field_value.get("values") or ()
    normalized_options = [
        item.get("value", item.get("id", item.get("label", "")))
        if isinstance(item, Mapping)
        else item
        for item in options
    ]
    if kind in {"radio", "select"}:
        if isinstance(value, bool):
            value = "yes" if value else "no"
        matched = next(
            (
                option
                for option in normalized_options
                if str(option).casefold() == str(value).casefold()
            ),
            None,
        )
        return matched
    if kind in {"checkbox", "boolean"}:
        if isinstance(value, bool):
            return value
        lowered = str(value).strip().casefold()
        if lowered in {"yes", "true", "1", "да"}:
            return True
        if lowered in {"no", "false", "0", "нет"}:
            return False
        return None
    if kind == "number":
        return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None
    text = str(value).strip()
    if kind == "email" and ("@" not in text or text.startswith("@")):
        return None
    if kind in {"tel", "phone"} and len(re.sub(r"\D", "", text)) < 7:
        return None
    return text or None


def _missing(value: Any) -> bool:
    return value is None or value == "" or value == () or value == []


__all__ = [
    "ChallengeResult",
    "FIELD_SOURCES",
    "GroundedAnswerMapper",
    "HHChallengeHandler",
    "MappingResult",
    "normalize_field_id",
    "sanitize_hh_url",
]
