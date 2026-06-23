from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class SourceCapabilities:
    search: str
    detail: str
    apply: str
    auth: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def normalize_source_capabilities(capabilities: dict[str, str]) -> SourceCapabilities:
    return SourceCapabilities(
        search=_normalize_search(capabilities.get("search", "")),
        detail=_normalize_detail(capabilities.get("detail", "")),
        apply=_normalize_apply(capabilities.get("apply", "")),
        auth=_normalize_auth(capabilities.get("auth", "")),
    )


def _normalize_search(value: str) -> str:
    return {
        "": "none",
        "none": "none",
        "official_api": "official_api",
        "public_json": "public_json",
        "frontend_json": "public_json",
        "public_html": "public_html",
        "html_listing": "public_html",
        "public_html_or_browser": "public_html",
        "public_channels": "public_html",
        "personal_auth_recon": "authenticated_browser",
    }.get(str(value or ""), str(value or "none"))


def _normalize_detail(value: str) -> str:
    return {
        "": "none",
        "none": "none",
        "official_api": "official_api",
        "public_json": "public_json",
        "listing_payload": "public_json",
        "message_payload": "public_json",
        "public_html": "public_html",
        "html_detail": "public_html",
        "public_html_or_browser": "public_html",
        "personal_auth_recon": "authenticated_browser",
    }.get(str(value or ""), str(value or "none"))


def _normalize_apply(value: str) -> str:
    return {
        "": "none",
        "none": "none",
        "official_api": "official_api",
        "external_page": "external_link",
        "external_contact": "external_link",
        "form_fill": "form_fill",
        "browser_submit": "browser_submit",
        "personal_auth_recon": "browser_submit",
    }.get(str(value or ""), str(value or "none"))


def _normalize_auth(value: str) -> str:
    return {
        "": "none",
        "none": "none",
        "api_key": "api_key",
        "oauth": "oauth",
        "browser_session": "browser_session",
        "cookie_session": "cookie_session",
    }.get(str(value or ""), str(value or "none"))
