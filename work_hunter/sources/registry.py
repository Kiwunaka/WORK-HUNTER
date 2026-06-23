from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


SOURCE_CAPABILITIES: dict[str, dict[str, str]] = {
    "hh": {
        "search": "official_api",
        "detail": "official_api",
        "apply": "official_api",
        "auth": "oauth",
    },
    "habr": {
        "search": "frontend_json",
        "detail": "listing_payload",
        "apply": "external_page",
        "auth": "none",
    },
    "geekjob": {
        "search": "public_json",
        "detail": "listing_payload",
        "apply": "external_page",
        "auth": "none",
    },
    "getmatch": {
        "search": "public_json",
        "detail": "public_json",
        "apply": "personal_auth_recon",
        "auth": "browser_session",
    },
    "relocate_me": {
        "search": "html_listing",
        "detail": "html_detail",
        "apply": "external_page",
        "auth": "none",
    },
    "rvc": {
        "search": "personal_auth_recon",
        "detail": "personal_auth_recon",
        "apply": "personal_auth_recon",
        "auth": "browser_session",
    },
    "hirehi": {
        "search": "public_html",
        "detail": "public_html",
        "apply": "external_page",
        "auth": "none",
    },
    "careerspace": {
        "search": "public_html_or_browser",
        "detail": "public_html_or_browser",
        "apply": "external_page",
        "auth": "none",
    },
    "another_it": {
        "search": "public_html",
        "detail": "public_html",
        "apply": "external_page",
        "auth": "none",
    },
    "jabka": {
        "search": "public_html",
        "detail": "public_html",
        "apply": "external_page",
        "auth": "none",
    },
    "telegram": {
        "search": "public_channels",
        "detail": "message_payload",
        "apply": "external_contact",
        "auth": "none",
    },
}


EXTERNAL_APPLY_EVIDENCE_REQUIREMENTS = ("tests", "replay", "redaction", "dry_run")


DEDICATED_ADAPTERS = {
    "hh": "work_hunter.sources.hh.HHSource",
    "habr": "work_hunter.sources.habr.HabrSource",
    "geekjob": "work_hunter.sources.geekjob.GeekJobSource",
    "getmatch": "work_hunter.sources.getmatch.GetmatchSource",
    "relocate_me": "work_hunter.sources.relocate_me.RelocateMeSource",
    "telegram": "work_hunter.sources.telegram.TelegramSource",
}


GENERIC_PUBLIC_BOARD_SOURCES = {"hirehi", "rvc", "careerspace", "another_it", "jabka"}


@dataclass(frozen=True)
class SourceAdapterSpec:
    name: str
    adapter_class: str
    adapter_status: str
    capabilities: dict[str, str]
    level: int
    readiness_badge: str
    blockers: list[str]

    def to_status(self, source_config: dict[str, Any] | None = None) -> dict[str, Any]:
        config = source_config or {}
        level = _configured_level(self.level, config)
        blockers = _configured_blockers(self.blockers, config, level)
        can_real_apply = level >= 5 and not blockers
        can_campaign_apply = level >= 6 and not blockers
        return {
            **asdict(self),
            **self.capabilities,
            "enabled": bool(config.get("enabled", False)),
            "level": level,
            "level_name": f"L{level}",
            "readiness_badge": _readiness_badge(
                level,
                official_api=self.capabilities.get("apply") == "official_api",
            ),
            "blockers": blockers,
            "can_collect": level >= 0,
            "can_detail": level >= 1,
            "can_prepare_apply": level >= 2,
            "can_real_apply": can_real_apply,
            "can_campaign_apply": can_campaign_apply,
            "real_apply_channel": _real_apply_channel(level, self.capabilities, config) if can_real_apply else "",
        }


def source_registry() -> dict[str, SourceAdapterSpec]:
    return {
        name: SourceAdapterSpec(
            name=name,
            adapter_class=_adapter_class(name),
            adapter_status=_adapter_status(name),
            capabilities=capabilities,
            level=_source_maturity_level(name, capabilities),
            readiness_badge=_readiness_badge(
                _source_maturity_level(name, capabilities),
                official_api=capabilities.get("apply") == "official_api",
            ),
            blockers=_blockers(name, capabilities),
        )
        for name, capabilities in SOURCE_CAPABILITIES.items()
    }


def source_status_report(source_configs: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    configs = source_configs or {}
    return {
        name: spec.to_status(dict(configs.get(name) or {}))
        for name, spec in source_registry().items()
    }


def _adapter_class(name: str) -> str:
    if name in DEDICATED_ADAPTERS:
        return DEDICATED_ADAPTERS[name]
    if name in GENERIC_PUBLIC_BOARD_SOURCES:
        return "work_hunter.sources.public_boards.PublicJobBoardSource"
    return ""


def _adapter_status(name: str) -> str:
    if name in DEDICATED_ADAPTERS:
        return "implemented"
    if name in GENERIC_PUBLIC_BOARD_SOURCES:
        return "generic_public_board"
    return "missing"


def _source_maturity_level(source_name: str, capabilities: dict[str, str]) -> int:
    if source_name == "hh" and capabilities.get("apply") == "official_api":
        return 6
    apply_capability = capabilities.get("apply", "")
    detail_capability = capabilities.get("detail", "")
    search_capability = capabilities.get("search", "")
    if apply_capability == "personal_auth_recon":
        return 2
    if apply_capability == "external_page":
        return 2 if detail_capability not in {"unknown", ""} else 1
    if search_capability and detail_capability:
        return 1
    return 0


def _readiness_badge(level: int, *, official_api: bool = False) -> str:
    if level >= 6:
        return "L6 real apply" if official_api else "L6 campaign apply"
    if level >= 5:
        return "L5 manual-confirm apply"
    if level >= 2:
        return "L2 apply plan"
    if level >= 1:
        return "L1 detail"
    return "L0 search"


def _configured_level(default_level: int, config: dict[str, Any]) -> int:
    external_apply = config.get("external_apply") or {}
    if not isinstance(external_apply, dict) or not external_apply.get("certified"):
        return default_level
    configured = _requested_external_apply_level(external_apply, default_level=default_level)
    if configured >= 5 and _external_apply_certification_blockers(external_apply):
        return default_level
    return max(default_level, min(6, configured))


def _configured_blockers(blockers: list[str], config: dict[str, Any], level: int) -> list[str]:
    result = list(blockers)
    external_apply = config.get("external_apply") or {}
    if isinstance(external_apply, dict) and external_apply.get("certified"):
        requested = _requested_external_apply_level(external_apply, default_level=0)
        if requested >= 5:
            certification_blockers = _external_apply_certification_blockers(external_apply)
            if certification_blockers:
                result.extend(certification_blockers)
            else:
                result = [
                    item
                    for item in result
                    if item
                    not in {
                        "real_apply_maturity_below_l4",
                        "browser_session_required",
                        "apply_mapping_requires_recon",
                    }
                ]
    return list(dict.fromkeys(result))


def _real_apply_channel(level: int, capabilities: dict[str, str], config: dict[str, Any]) -> str:
    if capabilities.get("apply") == "official_api" and level >= 6:
        return "official_api"
    external_apply = config.get("external_apply") or {}
    if isinstance(external_apply, dict) and external_apply.get("certified") and level >= 5:
        return "certified_external_session"
    return ""


def _blockers(name: str, capabilities: dict[str, str]) -> list[str]:
    blockers: list[str] = []
    if _adapter_status(name) == "missing":
        blockers.append("adapter_missing")
    if _source_maturity_level(name, capabilities) < 4:
        blockers.append("real_apply_maturity_below_l4")
    if capabilities.get("auth") == "browser_session":
        blockers.append("browser_session_required")
    if capabilities.get("apply") == "personal_auth_recon":
        blockers.append("apply_mapping_requires_recon")
    return blockers


def _requested_external_apply_level(external_apply: dict[str, Any], *, default_level: int) -> int:
    try:
        return max(0, min(6, int(external_apply.get("level") or external_apply.get("maturity_level") or default_level)))
    except (TypeError, ValueError):
        return default_level


def _external_apply_certification_blockers(external_apply: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    if not external_apply.get("session"):
        blockers.append("external_apply_session_missing")
    if not external_apply.get("url"):
        blockers.append("external_apply_url_missing")
    evidence = _external_apply_evidence(external_apply)
    for requirement in EXTERNAL_APPLY_EVIDENCE_REQUIREMENTS:
        if not _evidence_is_present(evidence.get(requirement) if requirement in evidence else external_apply.get(requirement)):
            blockers.append(f"external_apply_{requirement}_missing")
    return blockers


def _external_apply_evidence(external_apply: dict[str, Any]) -> dict[str, Any]:
    for key in ("evidence", "certification"):
        value = external_apply.get(key)
        if isinstance(value, dict):
            return value
    return {}


def _evidence_is_present(value: Any) -> bool:
    if value is True:
        return True
    if value in (False, None, ""):
        return False
    if isinstance(value, dict):
        status = str(value.get("status") or value.get("result") or "").strip().lower()
        if status in {"ok", "pass", "passed", "ready", "complete", "completed", "dry_run_ready", "executed_dry_run"}:
            return True
        return any(value.get(key) for key in ("id", "event_id", "run_id", "scan_id", "command", "path", "hash"))
    if isinstance(value, str):
        lowered = value.strip().lower()
        return bool(lowered) and lowered not in {"false", "no", "fail", "failed", "missing"}
    return bool(value)
