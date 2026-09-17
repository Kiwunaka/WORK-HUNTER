from __future__ import annotations

from work_hunter.hh_transport.oauth import (
    ANDROID_CLIENT_ID,
    ANDROID_CLIENT_SECRET,
    HHOAuthCredentials,
    build_authorize_url,
    credentials_from_config,
    extract_authorization_code,
    resolve_credentials,
)


def test_build_authorize_url_minimal() -> None:
    url = build_authorize_url(HHOAuthCredentials(client_id="CID", client_secret="x"))
    assert "client_id=CID" in url
    assert "response_type=code" in url
    # Фиксированный redirect Android-приложения — иначе десктопный браузер
    # не откроет hhandroid:// редирект.
    assert "redirect_uri=hhandroid" in url


def test_build_authorize_url_with_scope() -> None:
    url = build_authorize_url(
        HHOAuthCredentials(client_id="CID", client_secret="x", scope="applicant")
    )
    assert "client_id=CID" in url
    assert "response_type=code" in url
    assert "scope=applicant" in url


def test_extract_authorization_code_from_hhandroid_redirect() -> None:
    assert (
        extract_authorization_code("hhandroid://oauth?code=ABC123") == "ABC123"
    )
    # Реальный формат HH без слэша: hhandroid://oauthresponse?code=...
    assert (
        extract_authorization_code(
            "hhandroid://oauthresponse?code=NBF6JOMU244NQS07116PSTUQ"
        )
        == "NBF6JOMU244NQS07116PSTUQ"
    )


def test_credentials_from_config_rejects_mask_and_empty() -> None:
    assert credentials_from_config({}) is None
    assert credentials_from_config({"client_id": "***", "client_secret": "***"}) is None
    assert credentials_from_config({"client_id": "CID", "client_secret": ""}) is None
    creds = credentials_from_config({"client_id": "CID", "client_secret": "SEC"})
    assert creds is not None
    assert creds.client_id == "CID"


def test_oauth_callback_rejects_non_hhandroid_scheme() -> None:
    import pytest

    with pytest.raises(ValueError):
        extract_authorization_code("https://hh.ru/oauth?code=ABC")
    with pytest.raises(ValueError):
        extract_authorization_code("hhandroid://oauth?nonsense=1")


def test_resolve_credentials_prefers_custom_over_android() -> None:
    resolved = resolve_credentials({})
    assert resolved.client_id == ANDROID_CLIENT_ID
    assert resolved.client_secret == ANDROID_CLIENT_SECRET

    custom = resolve_credentials({"client_id": "CID", "client_secret": "SEC"})
    assert custom.client_id == "CID"
    assert custom.client_secret == "SEC"
