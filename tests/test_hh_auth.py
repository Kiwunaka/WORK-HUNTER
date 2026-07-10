from __future__ import annotations

import copy
import json
import os
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from work_hunter.services import WorkHunter
from work_hunter.web.server import make_handler


class FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload
        self.headers: dict[str, str] = {}

    def json(self):
        return self._payload


class FakeRefreshHHClient:
    calls: list[str] = []

    def __init__(self, config, *, backend=None):
        self.config = config
        self.backend = backend

    def refresh_token(self):
        self.calls.append("refresh")
        token = {
            "access_token": "new-access",
            "refresh_token": "new-refresh",
            "expires_at": "2030-01-01T00:00:00+00:00",
        }
        if self.backend is not None:
            self.backend.save(
                {
                    "access_token": token["access_token"],
                    "refresh_token": token["refresh_token"],
                    "access_expires_at": token["expires_at"],
                }
            )
        return token


class FakeFailingRefreshHHClient:
    def __init__(self, config, *, backend=None):
        self.config = config
        self.backend = backend

    def refresh_token(self):
        raise RuntimeError("HH API error 400: invalid_grant")


def test_refresh_hh_token_saves_new_token_only_after_success(monkeypatch, tmp_path):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeRefreshHHClient)
    FakeRefreshHHClient.calls = []
    app = WorkHunter(root=tmp_path)
    app.config["sources"]["hh"]["access_token"] = "old-access"
    app.config["sources"]["hh"]["refresh_token"] = "old-refresh"
    app.save_config(app.config)

    result = app.refresh_hh_token()

    reloaded = WorkHunter(root=tmp_path)
    hh_config = reloaded.config["sources"]["hh"]
    assert result["status"] == "ok"
    assert result["access_token"] == "new-access"
    assert result["refresh_token"] == "new-refresh"
    assert hh_config["access_token"] == "new-access"
    assert hh_config["refresh_token"] == "new-refresh"
    assert FakeRefreshHHClient.calls == ["refresh"]


def test_refresh_hh_token_preserves_existing_token_on_failure(monkeypatch, tmp_path):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeFailingRefreshHHClient)
    app = WorkHunter(root=tmp_path)
    app.config["sources"]["hh"]["access_token"] = "old-access"
    app.config["sources"]["hh"]["refresh_token"] = "old-refresh"
    app.save_config(app.config)

    with pytest.raises(RuntimeError):
        app.refresh_hh_token()

    reloaded = WorkHunter(root=tmp_path)
    hh_config = reloaded.config["sources"]["hh"]
    assert hh_config["access_token"] == "old-access"
    assert hh_config["refresh_token"] == "old-refresh"


def test_rotated_refresh_token_persists_to_client_app_and_disk(monkeypatch, tmp_path):
    app = WorkHunter(tmp_path)
    app.save_hh_account_profile(
        "personal",
        access_token="expired-access",
        refresh_token="old-refresh",
        access_expires_at="2000-01-01T00:00:00+00:00",
    )
    app.use_hh_account_profile("personal")

    token_response = FakeResponse(
        200,
        {
            "access_token": "new-access",
            "refresh_token": "new-refresh",
            "expires_at": "2031-01-01T00:00:00+00:00",
        },
    )
    me_response = FakeResponse(200, {"id": "me-1"})
    monkeypatch.setattr("requests.request", lambda *args, **kwargs: token_response)
    monkeypatch.setattr(
        "requests.sessions.Session.request",
        lambda *args, **kwargs: me_response,
    )

    client = app._hh_client()
    assert client.whoami() == {"id": "me-1"}
    assert client.session.identity.refresh_token == "new-refresh"
    assert app.hh_config()["refresh_token"] == "new-refresh"

    reloaded = WorkHunter(tmp_path)
    assert reloaded.hh_config()["access_token"] == "new-access"
    assert reloaded.hh_config()["refresh_token"] == "new-refresh"
    assert reloaded._hh_client().session.identity.refresh_token == "new-refresh"


def test_rotated_default_refresh_token_updates_account_and_source(monkeypatch, tmp_path):
    app = WorkHunter(tmp_path)
    app.save_hh_account_profile(
        "default",
        access_token="expired-access",
        refresh_token="old-refresh",
        access_expires_at="2000-01-01T00:00:00+00:00",
    )

    token_response = FakeResponse(
        200,
        {
            "access_token": "new-access",
            "refresh_token": "new-refresh",
            "expires_at": "2031-01-01T00:00:00+00:00",
        },
    )
    monkeypatch.setattr("requests.request", lambda *args, **kwargs: token_response)
    monkeypatch.setattr(
        "requests.sessions.Session.request",
        lambda *args, **kwargs: FakeResponse(200, {"id": "me-1"}),
    )

    assert app._hh_client().whoami() == {"id": "me-1"}
    assert app.hh_config()["refresh_token"] == "new-refresh"
    assert app.config["hh_account_profiles"]["default"]["refresh_token"] == "new-refresh"
    assert app.config["sources"]["hh"]["refresh_token"] == "new-refresh"

    reloaded = WorkHunter(tmp_path)
    assert reloaded.hh_config()["refresh_token"] == "new-refresh"


def test_named_account_collector_rotation_persists_to_app_and_disk(monkeypatch, tmp_path):
    app = WorkHunter(tmp_path)
    app.config["sources"]["hh"].update(
        {
            "access_token": "default-expired-access",
            "refresh_token": "default-old-refresh",
            "access_expires_at": "2000-01-01T00:00:00+00:00",
            "web_fallback": False,
            "pages": 1,
        }
    )
    app.config["profiles"]["default"]["queries"] = ["python"]
    app.save_hh_account_profile(
        "personal",
        access_token="personal-expired-access",
        refresh_token="personal-old-refresh",
        access_expires_at="2000-01-01T00:00:00+00:00",
    )
    app.use_hh_account_profile("personal")
    app.save_config(app.config)

    refresh_payloads: list[dict[str, str]] = []

    def token_request(*args, **kwargs):
        refresh_payloads.append(dict(kwargs["data"]))
        return FakeResponse(
            200,
            {
                "access_token": "collector-new-access",
                "refresh_token": "collector-new-refresh",
                "expires_at": "2031-01-01T00:00:00+00:00",
            },
        )

    monkeypatch.setattr("requests.request", token_request)
    monkeypatch.setattr(
        "requests.sessions.Session.request",
        lambda *args, **kwargs: FakeResponse(200, {"items": []}),
    )

    result = app.sync_sources(sources=["hh"], limit=1)

    assert result["hh"] == {"status": "ok", "count": 0}
    assert refresh_payloads[0]["refresh_token"] == "personal-old-refresh"
    assert app.hh_config()["refresh_token"] == "collector-new-refresh"
    assert app.config["sources"]["hh"]["refresh_token"] == "default-old-refresh"

    reloaded = WorkHunter(tmp_path)
    assert reloaded.hh_config()["access_token"] == "collector-new-access"
    assert reloaded.hh_config()["refresh_token"] == "collector-new-refresh"


def test_named_account_fallback_rotation_persists_to_disk(monkeypatch, tmp_path):
    app = WorkHunter(tmp_path)
    app.save_hh_account_profile(
        "personal",
        access_token="personal-expired-access",
        refresh_token="personal-old-refresh",
        access_expires_at="2000-01-01T00:00:00+00:00",
    )
    app.use_hh_account_profile("personal")
    refresh_payloads: list[dict[str, str]] = []

    def token_request(*args, **kwargs):
        refresh_payloads.append(dict(kwargs["data"]))
        return FakeResponse(
            200,
            {
                "access_token": "fallback-new-access",
                "refresh_token": "fallback-new-refresh",
                "expires_at": "2031-01-01T00:00:00+00:00",
            },
        )

    monkeypatch.setattr("requests.request", token_request)
    monkeypatch.setattr(
        "requests.sessions.Session.request",
        lambda *args, **kwargs: FakeResponse(200, {"items": []}),
    )

    result = app._search_hh_vacancies_web_fallback(
        text="python",
        area=None,
        salary=None,
        limit=1,
        reason="api_error",
    )

    assert result["status"] == "ok"
    assert result["count"] == 0
    assert refresh_payloads[0]["refresh_token"] == "personal-old-refresh"
    assert WorkHunter(tmp_path).hh_config()["refresh_token"] == "fallback-new-refresh"


def test_hh_client_pins_account_when_active_profile_changes(monkeypatch, tmp_path):
    app = WorkHunter(tmp_path)
    for name in ("alice", "bob"):
        app.save_hh_account_profile(
            name,
            access_token=f"{name}-expired-access",
            refresh_token=f"{name}-old-refresh",
            access_expires_at="2000-01-01T00:00:00+00:00",
        )
    app.use_hh_account_profile("alice")
    alice_client = app._hh_client()
    app.use_hh_account_profile("bob")

    monkeypatch.setattr(
        "requests.request",
        lambda *args, **kwargs: FakeResponse(
            200,
            {
                "access_token": "alice-new-access",
                "refresh_token": "alice-new-refresh",
                "expires_at": "2031-01-01T00:00:00+00:00",
            },
        ),
    )
    monkeypatch.setattr(
        "requests.sessions.Session.request",
        lambda *args, **kwargs: FakeResponse(200, {"id": "alice"}),
    )

    assert alice_client.whoami() == {"id": "alice"}

    reloaded = WorkHunter(tmp_path)
    accounts = reloaded.config["hh_account_profiles"]
    assert accounts["alice"]["refresh_token"] == "alice-new-refresh"
    assert accounts["bob"]["refresh_token"] == "bob-old-refresh"
    assert reloaded.config["hh_account_profile"] == "bob"


def test_hh_runtime_pins_account_and_credentials_from_same_snapshot(monkeypatch, tmp_path):
    app = WorkHunter(tmp_path)
    for name in ("alice", "bob"):
        app.save_hh_account_profile(
            name,
            access_token=f"{name}-expired-access",
            refresh_token=f"{name}-old-refresh",
            access_expires_at="2000-01-01T00:00:00+00:00",
        )
    app.use_hh_account_profile("alice")
    original_hh_config = app.hh_config
    switched_during_construction = False

    def switch_before_config_snapshot():
        nonlocal switched_during_construction
        switched_during_construction = True
        app.use_hh_account_profile("bob")
        return original_hh_config()

    monkeypatch.setattr(app, "hh_config", switch_before_config_snapshot)
    alice_client = app._hh_client()
    if not switched_during_construction:
        app.use_hh_account_profile("bob")

    refresh_payloads: list[dict[str, str]] = []

    def token_request(*args, **kwargs):
        refresh_payloads.append(dict(kwargs["data"]))
        return FakeResponse(
            200,
            {
                "access_token": "alice-new-access",
                "refresh_token": "alice-new-refresh",
                "expires_at": "2031-01-01T00:00:00+00:00",
            },
        )

    monkeypatch.setattr("requests.request", token_request)
    monkeypatch.setattr(
        "requests.sessions.Session.request",
        lambda *args, **kwargs: FakeResponse(200, {"id": "alice"}),
    )

    assert alice_client.whoami() == {"id": "alice"}
    assert refresh_payloads[0]["refresh_token"] == "alice-old-refresh"

    reloaded = WorkHunter(tmp_path)
    accounts = reloaded.config["hh_account_profiles"]
    assert accounts["alice"]["refresh_token"] == "alice-new-refresh"
    assert accounts["bob"]["refresh_token"] == "bob-old-refresh"
    assert reloaded.config["hh_account_profile"] == "bob"


def test_identity_patch_merges_with_fresh_disk_config(monkeypatch, tmp_path):
    stale_app = WorkHunter(tmp_path)
    stale_app.save_hh_account_profile(
        "personal",
        access_token="expired-access",
        refresh_token="old-refresh",
        access_expires_at="2000-01-01T00:00:00+00:00",
    )
    stale_app.use_hh_account_profile("personal")
    stale_client = stale_app._hh_client()

    newer_app = WorkHunter(tmp_path)
    newer_app.config["research"]["max_results"] = 777
    newer_app.config["about"]["summary"] = "newer-state"
    newer_app.save_config(newer_app.config)

    monkeypatch.setattr(
        "requests.request",
        lambda *args, **kwargs: FakeResponse(
            200,
            {
                "access_token": "new-access",
                "refresh_token": "new-refresh",
                "expires_at": "2031-01-01T00:00:00+00:00",
            },
        ),
    )
    monkeypatch.setattr(
        "requests.sessions.Session.request",
        lambda *args, **kwargs: FakeResponse(200, {"id": "me-1"}),
    )

    assert stale_client.whoami() == {"id": "me-1"}

    reloaded = WorkHunter(tmp_path)
    assert reloaded.config["research"]["max_results"] == 777
    assert reloaded.config["about"]["summary"] == "newer-state"
    assert reloaded.hh_config()["refresh_token"] == "new-refresh"
    assert stale_app.config["research"]["max_results"] == 777


def test_hh_runtime_reconciles_external_rotation_and_account_switch_with_local_edits(
    tmp_path,
):
    setup_app = WorkHunter(tmp_path)
    setup_app.save_hh_account_profile(
        "alice",
        access_token="alice-old-access",
        refresh_token="alice-old-refresh",
    )
    setup_app.save_hh_account_profile(
        "bob",
        access_token="bob-old-access",
        refresh_token="bob-old-refresh",
    )
    setup_app.use_hh_account_profile("alice")

    owner = WorkHunter(tmp_path)
    owner_alias = owner.config
    owner.config["about"]["summary"] = "unsaved-local-summary"
    external = WorkHunter(tmp_path)
    external._persist_hh_identity_patch(
        "alice",
        {
            "access_token": "alice-new-access",
            "refresh_token": "alice-new-refresh",
        },
    )

    alice_client = owner._hh_client()

    assert alice_client.session.identity.refresh_token == "alice-new-refresh"
    assert owner.config is owner_alias
    assert owner.config["about"]["summary"] == "unsaved-local-summary"

    alice_client.session.backend.save(
        {
            "access_token": "alice-runtime-access",
            "refresh_token": "alice-runtime-refresh",
        }
    )

    assert owner.config is owner_alias
    assert owner.config["about"]["summary"] == "unsaved-local-summary"
    assert (
        owner.config["hh_account_profiles"]["alice"]["refresh_token"]
        == "alice-runtime-refresh"
    )

    external._persist_hh_identity_patch(
        "bob",
        {
            "access_token": "bob-new-access",
            "refresh_token": "bob-new-refresh",
        },
    )
    external.use_hh_account_profile("bob")

    bob_client = owner._hh_client()

    assert bob_client.session.identity.refresh_token == "bob-new-refresh"
    assert owner.config["hh_account_profile"] == "bob"
    assert owner.config["about"]["summary"] == "unsaved-local-summary"

    owner.save_config(owner.config)
    reloaded = WorkHunter(tmp_path)
    assert reloaded.config["about"]["summary"] == "unsaved-local-summary"
    assert reloaded.config["hh_account_profile"] == "bob"
    assert reloaded.hh_config()["refresh_token"] == "bob-new-refresh"


def test_stale_workhunter_save_preserves_newer_identity_rotation(tmp_path):
    stale_app = WorkHunter(tmp_path)
    stale_app.save_hh_account_profile(
        "alice",
        access_token="alice-old-access",
        refresh_token="alice-old-refresh",
    )
    stale_app.use_hh_account_profile("alice")

    rotating_app = WorkHunter(tmp_path)
    rotating_app._persist_hh_identity_patch(
        "alice",
        {
            "access_token": "alice-new-access",
            "refresh_token": "alice-new-refresh",
        },
    )

    stale_app.config["about"]["summary"] = "unrelated-stale-writer"
    stale_app.save_config(stale_app.config)

    first_reload = WorkHunter(tmp_path)
    assert first_reload.config["about"]["summary"] == "unrelated-stale-writer"
    assert (
        first_reload.config["hh_account_profiles"]["alice"]["refresh_token"]
        == "alice-new-refresh"
    )
    assert (
        stale_app.config["hh_account_profiles"]["alice"]["refresh_token"]
        == "alice-new-refresh"
    )

    rotating_app._persist_hh_identity_patch(
        "alice",
        {
            "access_token": "alice-newer-access",
            "refresh_token": "alice-newer-refresh",
        },
    )
    stale_app.config["research"]["max_results"] = 321
    stale_app.save_config(stale_app.config)

    second_reload = WorkHunter(tmp_path)
    assert second_reload.config["research"]["max_results"] == 321
    assert (
        second_reload.config["hh_account_profiles"]["alice"]["refresh_token"]
        == "alice-newer-refresh"
    )


def test_config_alias_stays_canonical_after_save_and_identity_callback(tmp_path):
    app = WorkHunter(tmp_path)
    app.save_hh_account_profile(
        "alice",
        access_token="alice-old-access",
        refresh_token="alice-old-refresh",
    )
    config_alias = app.config

    rotating_app = WorkHunter(tmp_path)
    rotating_app._persist_hh_identity_patch(
        "alice",
        {
            "access_token": "alice-new-access",
            "refresh_token": "alice-new-refresh",
        },
    )

    config_alias["about"]["summary"] = "first-alias-save"
    app.save_config(config_alias)

    assert app.config is config_alias
    assert (
        config_alias["hh_account_profiles"]["alice"]["refresh_token"]
        == "alice-new-refresh"
    )

    app._persist_hh_identity_patch(
        "alice",
        {
            "access_token": "alice-newer-access",
            "refresh_token": "alice-newer-refresh",
        },
    )

    assert app.config is config_alias
    assert (
        config_alias["hh_account_profiles"]["alice"]["refresh_token"]
        == "alice-newer-refresh"
    )

    separate_mapping = copy.deepcopy(config_alias)
    separate_mapping["research"]["max_results"] = 654
    app.save_config(separate_mapping)

    assert app.config is separate_mapping
    assert separate_mapping == config_alias
    separate_mapping["about"]["summary"] = "unsaved-via-accepted-mapping"
    app._persist_hh_identity_patch(
        "alice",
        {
            "access_token": "alice-final-access",
            "refresh_token": "alice-final-refresh",
        },
    )

    assert (
        separate_mapping["hh_account_profiles"]["alice"]["refresh_token"]
        == "alice-final-refresh"
    )
    assert separate_mapping["about"]["summary"] == "unsaved-via-accepted-mapping"
    assert (
        config_alias["hh_account_profiles"]["alice"]["refresh_token"]
        == "alice-final-refresh"
    )

    config_alias["research"]["max_results"] = 655
    app.save_config(config_alias)
    assert app.config is config_alias
    assert (
        WorkHunter(tmp_path).config["hh_account_profiles"]["alice"]["refresh_token"]
        == "alice-final-refresh"
    )

    app._persist_hh_identity_patch(
        "alice",
        {
            "access_token": "alice-ultimate-access",
            "refresh_token": "alice-ultimate-refresh",
        },
    )
    assert (
        separate_mapping["hh_account_profiles"]["alice"]["refresh_token"]
        == "alice-ultimate-refresh"
    )
    assert (
        config_alias["hh_account_profiles"]["alice"]["refresh_token"]
        == "alice-ultimate-refresh"
    )

    separate_mapping["research"]["max_results"] = 656
    app.save_config(separate_mapping)
    assert app.config is separate_mapping

    reloaded = WorkHunter(tmp_path)
    assert reloaded.config["research"]["max_results"] == 656
    assert (
        reloaded.config["hh_account_profiles"]["alice"]["refresh_token"]
        == "alice-ultimate-refresh"
    )


def test_stale_workhunter_save_defines_explicit_secret_delete_and_list_changes(tmp_path):
    setup_app = WorkHunter(tmp_path)
    setup_app.save_hh_account_profile(
        "alice",
        access_token="alice-old-access",
        refresh_token="alice-old-refresh",
    )
    setup_app.config["about"]["temporary_note"] = "remove-me"
    setup_app.save_config(setup_app.config)

    stale_app = WorkHunter(tmp_path)
    newer_app = WorkHunter(tmp_path)
    newer_app.config["research"]["max_results"] = 777
    newer_app.save_config(newer_app.config)

    stale_app.config["hh_account_profiles"]["alice"][
        "refresh_token"
    ] = "alice-intentional-refresh"
    del stale_app.config["about"]["temporary_note"]
    stale_app.config["profiles"]["default"]["queries"] = [
        "python platform",
        "distributed systems",
    ]
    stale_app.save_config(stale_app.config)

    reloaded = WorkHunter(tmp_path)
    assert reloaded.config["research"]["max_results"] == 777
    assert (
        reloaded.config["hh_account_profiles"]["alice"]["refresh_token"]
        == "alice-intentional-refresh"
    )
    assert "temporary_note" not in reloaded.config["about"]
    assert reloaded.config["profiles"]["default"]["queries"] == [
        "python platform",
        "distributed systems",
    ]


def test_named_account_writer_applies_explicit_value_equal_to_stale_baseline(tmp_path):
    stale_app = WorkHunter(tmp_path)
    stale_app.save_hh_account_profile(
        "alice",
        access_token="alice-old-access",
        refresh_token="alice-old-refresh",
    )

    rotating_app = WorkHunter(tmp_path)
    rotating_app._persist_hh_identity_patch(
        "alice",
        {
            "access_token": "alice-new-access",
            "refresh_token": "alice-new-refresh",
        },
    )

    stale_app.save_hh_account_profile(
        "alice",
        refresh_token="alice-old-refresh",
    )

    reloaded = WorkHunter(tmp_path)
    assert (
        reloaded.config["hh_account_profiles"]["alice"]["refresh_token"]
        == "alice-old-refresh"
    )
    assert (
        reloaded.config["hh_account_profiles"]["alice"]["access_token"]
        == "alice-new-access"
    )


def test_identity_callback_refreshes_three_way_save_baseline(tmp_path):
    app = WorkHunter(tmp_path)
    app.save_hh_account_profile(
        "alice",
        access_token="alice-old-access",
        refresh_token="alice-old-refresh",
    )
    app._persist_hh_identity_patch(
        "alice",
        {
            "access_token": "alice-first-access",
            "refresh_token": "alice-first-refresh",
        },
    )

    another_app = WorkHunter(tmp_path)
    another_app._persist_hh_identity_patch(
        "alice",
        {
            "access_token": "alice-second-access",
            "refresh_token": "alice-second-refresh",
        },
    )

    app.config["about"]["summary"] = "saved-after-callback"
    app.save_config(app.config)

    reloaded = WorkHunter(tmp_path)
    assert reloaded.config["about"]["summary"] == "saved-after-callback"
    assert (
        reloaded.config["hh_account_profiles"]["alice"]["refresh_token"]
        == "alice-second-refresh"
    )


def test_explicit_refresh_persists_rotation_once(monkeypatch, tmp_path):
    app = WorkHunter(tmp_path)
    app.save_hh_account_profile(
        "personal",
        access_token="expired-access",
        refresh_token="old-refresh",
        access_expires_at="2000-01-01T00:00:00+00:00",
    )
    app.use_hh_account_profile("personal")

    monkeypatch.setattr(
        "requests.request",
        lambda *args, **kwargs: FakeResponse(
            200,
            {
                "access_token": "new-access",
                "refresh_token": "new-refresh",
                "expires_at": "2031-01-01T00:00:00+00:00",
            },
        ),
    )
    replacements: list[Path] = []
    original_replace = os.replace

    def record_replace(source, destination):
        replacements.append(Path(destination))
        original_replace(source, destination)

    monkeypatch.setattr(os, "replace", record_replace)

    result = app.refresh_hh_token()

    assert result["refresh_token"] == "new-refresh"
    assert replacements == [app.config_path]


def test_refresh_result_stays_pinned_when_active_account_switches_mid_request(
    monkeypatch,
    tmp_path,
):
    app = WorkHunter(tmp_path)
    app.save_hh_account_profile(
        "alice",
        access_token="alice-old-access",
        refresh_token="alice-old-refresh",
    )
    app.save_hh_account_profile(
        "bob",
        access_token="bob-old-access",
        refresh_token="bob-old-refresh",
    )
    app.use_hh_account_profile("alice")

    def switch_to_bob_during_refresh(*args, **kwargs):
        app.use_hh_account_profile("bob")
        return FakeResponse(
            200,
            {
                "access_token": "alice-new-access",
                "refresh_token": "alice-new-refresh",
                "expires_at": "2032-01-01T00:00:00+00:00",
            },
        )

    monkeypatch.setattr("requests.request", switch_to_bob_during_refresh)

    result = app.refresh_hh_token()

    assert result == {
        "status": "ok",
        "access_token": "alice-new-access",
        "refresh_token": "alice-new-refresh",
        "access_expires_at": "2032-01-01T00:00:00+00:00",
    }
    assert app.config["hh_account_profile"] == "bob"
    reloaded = WorkHunter(tmp_path)
    assert reloaded.config["hh_account_profile"] == "bob"
    assert (
        reloaded.config["hh_account_profiles"]["alice"]["refresh_token"]
        == "alice-new-refresh"
    )
    assert (
        reloaded.config["hh_account_profiles"]["bob"]["refresh_token"]
        == "bob-old-refresh"
    )


def test_hh_refresh_cli_outputs_masked_result(monkeypatch, tmp_path, capsys):
    from work_hunter.cli import main as cli_main

    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeRefreshHHClient)
    app = WorkHunter(root=tmp_path)
    app.config["sources"]["hh"]["refresh_token"] = "old-refresh"
    app.save_config(app.config)

    cli_main(["--root", str(tmp_path), "hh-refresh-token"])

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ok"
    assert payload["access_token"] == "***"
    assert payload["refresh_token"] == "***"


def test_hh_refresh_web_api_masks_tokens(monkeypatch, tmp_path):
    monkeypatch.setattr("work_hunter.services.HHApplyClient", FakeRefreshHHClient)
    app = WorkHunter(root=tmp_path)
    app.config["sources"]["hh"]["refresh_token"] = "old-refresh"
    app.save_config(app.config)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(tmp_path))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/api/hh/token/refresh",
            data=b"{}",
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    assert payload["status"] == "ok"
    assert payload["access_token"] == "***"
    assert payload["refresh_token"] == "***"
