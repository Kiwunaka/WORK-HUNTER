from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .capabilities import SourceCapabilities, normalize_source_capabilities


class SourceAdapter:
    name: str

    def capabilities(self) -> SourceCapabilities:
        raise NotImplementedError

    def status(self) -> dict[str, Any]:
        raise NotImplementedError

    def search(self, query: dict[str, Any]) -> list[dict[str, Any]]:
        raise NotImplementedError

    def detail(self, source_id: str) -> dict[str, Any]:
        raise NotImplementedError

    def prepare_apply(self, job: dict[str, Any], pack: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def dry_run_apply(self, plan: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def apply(self, plan: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError


@dataclass(frozen=True)
class RegisteredSourceAdapter(SourceAdapter):
    name: str
    raw_capabilities: dict[str, str]
    status_payload: dict[str, Any]
    source_client: Any | None = None

    def capabilities(self) -> SourceCapabilities:
        return normalize_source_capabilities(self.raw_capabilities)

    def status(self) -> dict[str, Any]:
        return dict(self.status_payload)

    def search(self, query: dict[str, Any]) -> list[dict[str, Any]]:
        client = self.source_client
        if client is None:
            return []
        if hasattr(client, "search"):
            return [_to_dict(item) for item in client.search(query)]
        if hasattr(client, "collect"):
            text = str(query.get("query") or query.get("text") or "")
            limit = query.get("limit")
            jobs = client.collect({"queries": [text]}, limit=limit)
            return [_to_dict(item) for item in jobs]
        return []

    def detail(self, source_id: str) -> dict[str, Any]:
        client = self.source_client
        if client is not None:
            if hasattr(client, "detail"):
                return {
                    "status": "ready",
                    "source": self.name,
                    "source_id": source_id,
                    "detail": _to_dict(client.detail(source_id)),
                }
            if hasattr(client, "fetch_detail"):
                return {
                    "status": "ready",
                    "source": self.name,
                    "source_id": source_id,
                    "detail": _to_dict(client.fetch_detail(source_id)),
                }
            if hasattr(client, "get_offer"):
                return {
                    "status": "ready",
                    "source": self.name,
                    "source_id": source_id,
                    "detail": _to_dict(client.get_offer(source_id)),
                }
        return {
            "status": "not_implemented",
            "source": self.name,
            "source_id": source_id,
            "reason": "adapter_detail_executor_not_wired",
        }

    def prepare_apply(self, job: dict[str, Any], pack: dict[str, Any]) -> dict[str, Any]:
        capability = self.capabilities().apply
        if capability == "external_link":
            return {
                "status": "external_link",
                "source": self.name,
                "source_id": str(job.get("source_id") or ""),
                "url": str(job.get("url") or ""),
                "application_pack_id": str(pack.get("id") or ""),
                "submit": False,
                "requires_manual_submit": True,
            }
        if capability == "official_api":
            return {
                "status": "official_api",
                "source": self.name,
                "source_id": str(job.get("source_id") or ""),
                "application_pack_id": str(pack.get("id") or ""),
                "submit": False,
                "requires_service_executor": True,
            }
        return {
            "status": "blocked",
            "source": self.name,
            "reason": "source_adapter_apply_not_supported",
            "submit": False,
        }

    def dry_run_apply(self, plan: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": "manual_review_required",
            "source": self.name,
            "plan": dict(plan),
            "submit": False,
            "reason": "adapter_specific_executor_not_configured",
        }

    def apply(self, plan: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
        if not policy.get("can_apply"):
            return {
                "status": "blocked",
                "source": self.name,
                "reason": "source_adapter_real_apply_not_enabled",
                "submit": False,
            }
        plan_payload = dict(plan)
        if policy.get("requires_confirmation") and plan_payload.get("status") == "external_link":
            external_url = str(plan_payload.get("url") or plan_payload.get("external_url") or "")
            actions = [{"type": "open_url", "url": external_url}] if external_url else []
            return {
                "status": "manual_submit_ready",
                "source": self.name,
                "source_id": str(plan_payload.get("source_id") or ""),
                "external_url": external_url,
                "application_pack_id": str(plan_payload.get("application_pack_id") or ""),
                "submit": False,
                "requires_confirmation": True,
                "final_submit_requires_user": True,
                "actions": actions,
                "plan": plan_payload,
            }
        return {
            "status": "blocked",
            "source": self.name,
            "reason": "source_adapter_apply_requires_service_executor",
            "submit": False,
            "plan": plan_payload,
        }


def _to_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "to_dict"):
        result = value.to_dict()
        return dict(result) if isinstance(result, dict) else {"value": result}
    return {
        key: item
        for key, item in vars(value).items()
        if not key.startswith("_")
    }
