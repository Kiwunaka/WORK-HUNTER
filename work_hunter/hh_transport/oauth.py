"""HH OAuth через Android-приложение: authorize_url, перехват кода, обмен на токен.

Порт подхода https://github.com/s3rgeym/hh-applicant-tool (личное использование):
браузер идёт на https://hh.ru/oauth/authorize?client_id=...&response_type=code,
после логина HH редиректит на hhandroid://...?code=..., код меняем на
access/refresh через POST https://api.hh.ru/token.
"""
from __future__ import annotations

import time
import urllib.parse
from dataclasses import dataclass
from typing import Any

import requests

HH_OAUTH_BASE = "https://hh.ru/oauth"
HH_API_BASE = "https://api.hh.ru"
HH_ANDROID_SCHEME = "hhandroid"
HH_ANDROID_REDIRECT = "hhandroid://oauthresponse"

# Встроенные credentials Android-приложения HH — порт из оригинала
# https://github.com/s3rgeym/hh-applicant-tool (src/hh_applicant_tool/api/client_keys.py),
# только для личного использования. Свои client_id/client_secret из конфига
# всегда приоритетнее (см. resolve_credentials).
ANDROID_CLIENT_ID = "HIOMIAS39CA9DICTA7JIO64LQKQJF5AGIK74G9ITJKLNEDAOH5FHS5G1JI7FOEGD"
ANDROID_CLIENT_SECRET = "V9M870DE342BGHFRUJ5FTCGCUA1482AN0DI8C5TFI9ULMA89H10N60NOP8I4JMVS"


@dataclass(frozen=True)
class HHOAuthCredentials:
    client_id: str
    client_secret: str
    redirect_uri: str = ""
    scope: str = ""
    state: str = ""


def build_authorize_url(credentials: HHOAuthCredentials) -> str:
    params = {
        "client_id": credentials.client_id,
        "response_type": "code",
        # Фиксированный redirect как у Android-приложения: иначе HH отдаёт
        # 302 на hhandroid://oauthresponse?code=..., который десктопный
        # браузер не может открыть и показывает ERR_UNKNOWN_URL_SCHEME.
        "redirect_uri": credentials.redirect_uri or HH_ANDROID_REDIRECT,
    }
    if credentials.scope:
        params["scope"] = credentials.scope
    if credentials.state:
        params["state"] = credentials.state
    return f"{HH_OAUTH_BASE}/authorize?{urllib.parse.urlencode(params)}"


def extract_authorization_code(redirect_url: str) -> str:
    """Вытащить ?code= из hhandroid:// редиректа.

    HH отдаёт hhandroid://oauthresponse?code=... (без слэша после схемы),
    urlsplit такой адрес кладёт всё в path — разбираем оба варианта.
    """
    raw = str(redirect_url or "").strip()
    parsed = urllib.parse.urlsplit(raw)
    if parsed.scheme.casefold() != HH_ANDROID_SCHEME:
        raise ValueError("OAuth redirect must use the hhandroid scheme")
    query = parsed.query
    if not query and "?" in raw:
        query = raw.split("?", 1)[1]
    code = (urllib.parse.parse_qs(query).get("code") or [""])[0].strip()
    if not code:
        raise ValueError("OAuth redirect has no authorization code")
    return code


def exchange_code_for_token(
    code: str,
    credentials: HHOAuthCredentials,
    *,
    user_agent: str = "",
    timeout: int = 30,
) -> dict[str, Any]:
    payload = {
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        "code": code.strip(),
        "grant_type": "authorization_code",
        # redirect_uri обязан совпадать с тем, что был в authorize.
        "redirect_uri": credentials.redirect_uri or HH_ANDROID_REDIRECT,
    }
    headers = {"Accept": "application/json"}
    if user_agent:
        headers["User-Agent"] = user_agent
        headers["HH-User-Agent"] = user_agent
    response = requests.request(
        "POST",
        f"{HH_API_BASE}/token",
        headers=headers,
        data=payload,
        timeout=max(1, int(timeout)),
    )
    try:
        body = response.json()
    except ValueError:
        body = {}
    if not 200 <= response.status_code < 300:
        raise RuntimeError(f"HH OAuth token exchange failed: HTTP {response.status_code}")
    if not isinstance(body, dict) or not body.get("access_token"):
        raise RuntimeError("HH OAuth token exchange returned no access_token")
    return {
        "access_token": str(body.get("access_token") or ""),
        "refresh_token": str(body.get("refresh_token") or ""),
        "access_expires_at": _expires_at_iso(body),
    }


def credentials_from_config(config: dict[str, Any]) -> HHOAuthCredentials | None:
    client_id = str(config.get("client_id") or "").strip()
    client_secret = str(config.get("client_secret") or "").strip()
    if not client_id or not client_secret or client_id == "***" or client_secret == "***":
        return None
    return HHOAuthCredentials(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=str(config.get("oauth_redirect_uri") or "").strip(),
        scope=str(config.get("oauth_scope") or "").strip(),
        state=str(config.get("oauth_state") or "").strip(),
    )


def resolve_credentials(config: dict[str, Any]) -> HHOAuthCredentials:
    """Свои credentials из конфига, иначе встроенные Android-ключи.

    Возвращает HHOAuthCredentials всегда — встроенные ключи порта оригинала
    гарантируют, что oauth-start работает из коробки без ручного ввода.
    """
    custom = credentials_from_config(config)
    if custom is not None:
        return custom
    return HHOAuthCredentials(
        client_id=ANDROID_CLIENT_ID,
        client_secret=ANDROID_CLIENT_SECRET,
    )


def _expires_at_iso(body: dict[str, Any]) -> str:
    for key in ("access_expires_at", "expires_at"):
        value = str(body.get(key) or "").strip()
        if value:
            return value
    try:
        seconds = int(body.get("expires_in") or 0)
    except (TypeError, ValueError):
        seconds = 0
    if seconds > 0:
        from datetime import datetime, timedelta, timezone

        return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()
    return ""


__all__ = [
    "ANDROID_CLIENT_ID",
    "ANDROID_CLIENT_SECRET",
    "HH_ANDROID_REDIRECT",
    "HH_ANDROID_SCHEME",
    "HH_API_BASE",
    "HH_OAUTH_BASE",
    "HHOAuthCredentials",
    "build_authorize_url",
    "credentials_from_config",
    "exchange_code_for_token",
    "extract_authorization_code",
    "resolve_credentials",
]
