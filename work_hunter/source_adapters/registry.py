from __future__ import annotations

from typing import Any

from ..sources.registry import source_registry as legacy_source_registry
from ..sources.registry import source_status_report as legacy_source_status_report
from .apply_policy import apply_policy_for_level
from .base import RegisteredSourceAdapter
from .capabilities import normalize_source_capabilities


def source_adapter_registry(source_configs: dict[str, Any] | None = None) -> dict[str, RegisteredSourceAdapter]:
    legacy = legacy_source_registry()
    configs = source_configs or {}
    return {
        name: RegisteredSourceAdapter(
            name=name,
            raw_capabilities=dict(spec.capabilities),
            status_payload=spec.to_status(_source_config(configs, name)),
            source_client=_source_client_for(name, _source_config(configs, name)),
        )
        for name, spec in legacy.items()
    }


def source_adapter_status_report(source_configs: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    legacy = legacy_source_status_report(source_configs)
    report: dict[str, dict[str, Any]] = {}
    for name, status in legacy.items():
        normalized = normalize_source_capabilities(_sdk_capabilities_for(name, status))
        level = int(status.get("level") or 0)
        report[name] = {
            "name": name,
            "enabled": bool(status.get("enabled", False)),
            "adapter_class": status.get("adapter_class"),
            "adapter_status": status.get("adapter_status"),
            "capabilities": normalized.to_dict(),
            "maturity": {
                "level": level,
                "level_name": f"L{level}",
                "readiness_badge": status.get("readiness_badge"),
                "blockers": list(status.get("blockers") or []),
            },
            "policy": {
                **apply_policy_for_level(level),
                "can_apply": bool(status.get("can_real_apply")),
                "can_campaign_apply": bool(status.get("can_campaign_apply")),
                "requires_confirmation": bool(status.get("can_real_apply")) and not bool(status.get("can_campaign_apply")),
            },
        }
    return report


def _sdk_capabilities_for(name: str, status: dict[str, Any]) -> dict[str, str]:
    if name in {"getmatch", "rvc"}:
        return {
            "search": "personal_auth_recon",
            "detail": "personal_auth_recon",
            "apply": str(status.get("apply") or ""),
            "auth": "browser_session",
        }
    return {
        "search": str(status.get("search") or ""),
        "detail": str(status.get("detail") or ""),
        "apply": str(status.get("apply") or ""),
        "auth": str(status.get("auth") or ""),
    }


def _source_config(source_configs: dict[str, Any], name: str) -> dict[str, Any]:
    config = source_configs.get(name) or {}
    return dict(config) if isinstance(config, dict) else {}


def _source_client_for(name: str, config: dict[str, Any]) -> Any | None:
    if name == "hh":
        from ..sources.hh import HHSource

        return HHSource(config)
    if name == "habr":
        from ..sources.habr import HabrSource

        return HabrSource(config)
    if name == "geekjob":
        from ..sources.geekjob import GeekJobSource

        return GeekJobSource(config)
    if name == "getmatch":
        from ..sources.getmatch import GetmatchSource

        return GetmatchSource(config)
    if name == "relocate_me":
        from ..sources.relocate_me import RelocateMeSource

        return RelocateMeSource(config)
    if name == "telegram":
        from ..sources.telegram import TelegramSource

        return TelegramSource(config)

    from ..sources.public_boards import PUBLIC_BOARD_SOURCE_NAMES, PublicJobBoardSource

    if name in PUBLIC_BOARD_SOURCE_NAMES:
        return PublicJobBoardSource(config, source_name=name)
    return None
