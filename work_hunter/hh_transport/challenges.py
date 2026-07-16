from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


ALLOWED_CHALLENGE_MODES = {"off", "manual", "ai"}


class ChallengeKind(StrEnum):
    CAPTCHA_REQUIRED = "captcha_required"
    TEST_REQUIRED = "test_required"
    MANUAL_FORM_REQUIRED = "manual_form_required"
    SOLVED = "solved"
    FAILED = "failed"
    NONE = "none"


@dataclass(frozen=True)
class ChallengeOutcome:
    kind: ChallengeKind
    message: str = ""
    url: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def blocked(self) -> bool:
        return self.kind in {
            ChallengeKind.CAPTCHA_REQUIRED,
            ChallengeKind.TEST_REQUIRED,
            ChallengeKind.MANUAL_FORM_REQUIRED,
            ChallengeKind.FAILED,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "message": self.message,
            "url": self.url,
            "blocked": self.blocked,
            "metadata": self.metadata,
        }


class HHChallengeHandler:
    def __init__(self, *, challenge_mode: str = "manual"):
        mode = str(challenge_mode or "manual").strip().lower()
        if mode not in ALLOWED_CHALLENGE_MODES:
            raise ValueError(f"Unsupported HH challenge_mode: {challenge_mode}")
        self.challenge_mode = mode

    def classify_apply_result(self, result: dict[str, Any]) -> ChallengeOutcome:
        status = str(result.get("status") or "")
        error = str(result.get("error") or "")
        location = _challenge_url(result)
        text = " ".join([status, error, location, str(result.get("message") or "")]).lower()
        if "captcha" in text:
            return self._blocked(ChallengeKind.CAPTCHA_REQUIRED, "Captcha is required", location)
        if "test" in text:
            return self._blocked(ChallengeKind.TEST_REQUIRED, "Vacancy test is required", location)
        if "form" in text or "response_url" in text or "redirect" in text:
            return self._blocked(ChallengeKind.MANUAL_FORM_REQUIRED, "Manual form is required", location)
        if status in {"created", "ok", "success"}:
            return ChallengeOutcome(
                ChallengeKind.SOLVED,
                "No challenge",
                metadata={"challenge_mode": self.challenge_mode},
            )
        if status in {"", "planned", "dry_run"}:
            return ChallengeOutcome(
                ChallengeKind.NONE,
                "",
                metadata={"challenge_mode": self.challenge_mode},
            )
        return self._blocked(ChallengeKind.FAILED, error or status, location)

    def _blocked(self, kind: ChallengeKind, message: str, url: str = "") -> ChallengeOutcome:
        metadata = {
            "challenge_mode": self.challenge_mode,
            "requires_approval": self.challenge_mode in {"manual", "ai"},
            "resolution": "manual_browser",
            "automatic_solver": False,
        }
        if self.challenge_mode == "off":
            metadata["disabled"] = True
        return ChallengeOutcome(kind, message, url, metadata)


def _challenge_url(result: dict[str, Any]) -> str:
    for key in ("response_url", "form_url", "manual_form_url", "redirect_url", "location", "url"):
        value = str(result.get(key) or "").strip()
        if value:
            return value
    return ""
