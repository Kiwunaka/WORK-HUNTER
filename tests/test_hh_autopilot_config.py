from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import pytest

from work_hunter.config import default_config, merge_masked_config
from work_hunter.hh_autopilot.config import (
    AutopilotConfigError,
    PolicyMaterial,
    RuntimeValidationContext,
    ValidationMode,
    canonicalize_policy_payload,
    parse_autopilot_settings,
    policy_hash,
    validate_run_mode,
    validate_runtime_dependencies,
)
from work_hunter.hh_autopilot.types import AuthorizationKind


def _autopilot(config: dict) -> dict:
    return config["sources"]["hh"]["autopilot"]


def _material() -> PolicyMaterial:
    return PolicyMaterial(
        effective_auth_profile_id="default",
        candidate_profile={
            "desired_roles": ["python", "backend"],
            "must_have_skills": ["python"],
        },
        candidate_profile_version="profile-v1",
        resumes=[
            {"id": "r1", "title": "Python", "content_hash": "resume-v1"},
            {"id": "r2", "title": "Backend", "content_hash": "resume-v2"},
        ],
        presets={"backend": {"text": "python", "area": ["1"]}},
        model_id="model-v1",
        prompt_versions={"ranking": "prompt-v1"},
        cover_letter_template_version="letter-v1",
        transport_identity={"kind": "api", "proxy_id": "direct"},
        secret_versions={"access_token": 1},
    )


def _runtime_context(config: dict | None = None) -> RuntimeValidationContext:
    runtime_config = deepcopy(config or default_config())
    runtime_config["hh_campaign_presets"] = {
        "backend": {
            "text": "python",
            "area": ["1"],
            "schedule": "remote",
            "employment": ["full"],
            "experience": "between1And3",
        }
    }
    return RuntimeValidationContext(
        config=runtime_config,
        published_resumes={
            "default": (
                {"id": "r1", "status": {"id": "published"}},
                {"id": "r2", "status": "published"},
            )
        },
        usable_application_transports={"default": ("api",)},
        dictionary_values={
            "schedules": frozenset({"remote"}),
            "employment_types": frozenset({"full"}),
            "experience_levels": frozenset({"between1and3"}),
        },
    )


def test_autopilot_defaults_are_disabled_and_bounded() -> None:
    settings = parse_autopilot_settings(default_config())
    account = settings.accounts[0]
    assert account.enabled is False
    assert settings.limits.daily_success == 50
    assert settings.search.per_page == 100
    assert settings.search.max_pages == 20


def test_invalid_lease_window_is_rejected() -> None:
    config = default_config()
    _autopilot(config)["lease"] = {
        "ttl_seconds": 30,
        "request_timeout_seconds": 30,
        "renewal_margin_seconds": 45,
    }
    with pytest.raises(AutopilotConfigError, match="lease"):
        parse_autopilot_settings(config)


def test_canary_needs_literal_confirmation_not_grant() -> None:
    settings = parse_autopilot_settings(default_config())
    kind = validate_run_mode(
        settings,
        account_id="default",
        mode=ValidationMode.CANARY,
        has_grant=False,
        literal_confirmed=True,
    )
    assert kind is AuthorizationKind.LITERAL_CONFIRMATION


def test_filter_change_invalidates_policy_hash() -> None:
    settings = parse_autopilot_settings(default_config())
    before = policy_hash(settings, "default", _material())
    changed = deepcopy(default_config())
    _autopilot(changed)["filters"]["excluded_keywords"] = ["1c"]
    after = policy_hash(parse_autopilot_settings(changed), "default", _material())
    assert before != after


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("search", "per_page"), 0),
        (("search", "max_pages"), 101),
        (("search", "max_results_per_run"), 10001),
        (("schedule", "interval_minutes"), 0),
        (("filters", "minimum_salary"), 1_000_000_001),
        (("limits", "administrative_max_daily_success"), 201),
        (("limits", "send_delay_min_seconds"), -1),
        (("retry", "max_attempts"), 21),
        (("retry", "base_delay_seconds"), 0),
        (("retry", "jitter_ratio"), 1.01),
        (("retry", "reconciliation_checks"), 0),
        (("lease", "ttl_seconds"), 601),
        (("application", "challenge_expiry_hours"), 721),
        (("browser", "navigation_timeout_seconds"), 0),
        (("retention", "event_days"), 3651),
        (("ranking", "minimum_score"), 101),
        (("ranking", "minimum_ai_confidence"), float("nan")),
    ],
)
def test_every_numeric_group_has_strict_bounds(path: tuple[str, str], value: object) -> None:
    config = default_config()
    group, key = path
    _autopilot(config)[group][key] = value
    with pytest.raises(AutopilotConfigError, match=rf"{group}\.{key}"):
        parse_autopilot_settings(config)


@pytest.mark.parametrize(
    ("group", "key", "value"),
    [
        ("search", "include_recommendations", 1),
        ("filters", "use_employer_blacklist", "true"),
        ("browser", "headless", 0),
        ("notifications", "challenge", None),
    ],
)
def test_boolean_fields_require_json_booleans(group: str, key: str, value: object) -> None:
    config = default_config()
    _autopilot(config)[group][key] = value
    with pytest.raises(AutopilotConfigError, match="boolean"):
        parse_autopilot_settings(config)


@pytest.mark.parametrize(
    ("group", "key", "value"),
    [
        ("filters", "remote", "sometimes"),
        ("filters", "unknown_salary", "guess"),
        ("ranking", "ai_mode", "automatic"),
        ("ranking", "ai_detail", "medium"),
        ("ranking", "ai_failure_policy", "allow"),
        ("application", "resume_policy", "all"),
        ("application", "cover_letter_mode", "manual"),
        ("application", "screening_mode", "ai"),
        ("application", "form_mode", "manual"),
        ("application", "captcha_mode", "solve"),
    ],
)
def test_modes_are_fixed_enums(group: str, key: str, value: str) -> None:
    config = default_config()
    _autopilot(config)[group][key] = value
    with pytest.raises(AutopilotConfigError, match=rf"{group}\.{key}"):
        parse_autopilot_settings(config)


def test_unknown_keys_are_rejected_recursively() -> None:
    config = default_config()
    _autopilot(config)["ranking"]["weights"]["custom_signal"] = 0.1
    with pytest.raises(AutopilotConfigError, match="unknown.*custom_signal"):
        parse_autopilot_settings(config)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda raw: raw.update({"timezone": "Mars/Olympus"}),
        lambda raw: raw["schedule"].update({"days": [1, 1]}),
        lambda raw: raw["schedule"].update({"start": "25:00"}),
        lambda raw: raw["schedule"].update({"start": "22:00", "end": "08:00"}),
        lambda raw: raw["limits"].update({"per_run_success": 51}),
        lambda raw: raw["limits"].update(
            {"send_delay_min_seconds": 100, "send_delay_max_seconds": 10}
        ),
        lambda raw: raw["ranking"].update(
            {"borderline_low": 70, "minimum_score": 60}
        ),
        lambda raw: raw["ranking"].update(
            {"weights": {key: 0 for key in raw["ranking"]["weights"]}}
        ),
        lambda raw: raw["retry"].update(
            {"base_delay_seconds": 100, "max_delay_seconds": 10}
        ),
    ],
)
def test_cross_field_invariants_are_enforced(mutate) -> None:
    config = default_config()
    mutate(_autopilot(config))
    with pytest.raises(AutopilotConfigError):
        parse_autopilot_settings(config)


def test_arrays_are_normalized_and_duplicates_rejected() -> None:
    config = default_config()
    _autopilot(config)["filters"]["excluded_keywords"] = [" Python ", "python"]
    with pytest.raises(AutopilotConfigError, match="duplicate"):
        parse_autopilot_settings(config)

    config = default_config()
    _autopilot(config)["accounts"][0]["resume_queries"][0]["preset_names"] = [
        " Backend ",
        "backend",
    ]
    with pytest.raises(AutopilotConfigError, match="duplicate"):
        parse_autopilot_settings(config)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("areas", ["not-an-hh-id"]),
        ("citizenships", [1]),
        ("languages", ["not_a_language"]),
        ("required_application_capabilities", ["telepathy"]),
        ("excluded_keywords", [""]),
        ("allowed_role_families", ["x" * 201]),
    ],
)
def test_filter_array_domains_are_enforced(field: str, value: list[object]) -> None:
    config = default_config()
    _autopilot(config)["filters"][field] = value
    with pytest.raises(AutopilotConfigError, match=field):
        parse_autopilot_settings(config)


def test_account_and_resume_mapping_domains_are_enforced() -> None:
    config = default_config()
    account = _autopilot(config)["accounts"][0]
    duplicate = deepcopy(account)
    duplicate["profile_id"] = " DEFAULT "
    _autopilot(config)["accounts"].append(duplicate)
    with pytest.raises(AutopilotConfigError, match="unique"):
        parse_autopilot_settings(config)

    config = default_config()
    _autopilot(config)["search"]["include_recommendations"] = False
    with pytest.raises(AutopilotConfigError, match="preset_names"):
        parse_autopilot_settings(config)


def test_runtime_dependencies_are_explicit_and_expand_resume_presets() -> None:
    config = default_config()
    _autopilot(config)["accounts"][0]["resume_queries"] = [
        {"resume_id": "published:*", "preset_names": ["backend"]}
    ]
    settings = parse_autopilot_settings(config)
    context = _runtime_context(config)

    expanded = validate_runtime_dependencies(settings, context)
    kind = validate_run_mode(
        settings,
        account_id="default",
        mode=ValidationMode.CANARY,
        has_grant=False,
        literal_confirmed=True,
        runtime_context=context,
    )

    assert {(item["resume_id"], item["preset_name"]) for item in expanded["default"]} == {
        ("r1", "backend"),
        ("r2", "backend"),
    }
    assert kind is AuthorizationKind.LITERAL_CONFIRMATION


@pytest.mark.parametrize(
    ("context_mutator", "message"),
    [
        (
            lambda context: replace(context, config={**context.config, "hh_account_profiles": {}}),
            "authentication profile",
        ),
        (
            lambda context: replace(context, config={**context.config, "profiles": {}}),
            "candidate profile",
        ),
        (
            lambda context: replace(context, published_resumes={"default": ()}),
            "published resume",
        ),
        (
            lambda context: replace(context, usable_application_transports={}),
            "transport",
        ),
    ],
)
def test_runtime_dependencies_reject_missing_external_state(context_mutator, message: str) -> None:
    settings = parse_autopilot_settings(default_config())
    context = context_mutator(_runtime_context())
    with pytest.raises(AutopilotConfigError, match=message):
        validate_runtime_dependencies(settings, context)


def test_runtime_rejects_unknown_preset_fields_and_duplicate_expansion() -> None:
    config = default_config()
    _autopilot(config)["accounts"][0]["resume_queries"] = [
        {"resume_id": "published:*", "preset_names": ["backend"]},
        {"resume_id": "r1", "preset_names": ["backend"]},
    ]
    settings = parse_autopilot_settings(config)
    context = _runtime_context(config)
    with pytest.raises(AutopilotConfigError, match="duplicate expanded"):
        validate_runtime_dependencies(settings, context)

    clean_config = default_config()
    _autopilot(clean_config)["accounts"][0]["resume_queries"] = [
        {"resume_id": "published:*", "preset_names": ["backend"]}
    ]
    context = _runtime_context(clean_config)
    context.config["hh_campaign_presets"]["backend"]["raw_query"] = "unsafe"
    with pytest.raises(AutopilotConfigError, match="unknown.*raw_query"):
        validate_runtime_dependencies(
            parse_autopilot_settings(clean_config),
            context,
        )


def test_dictionary_enums_are_checked_against_runtime_dictionary() -> None:
    config = default_config()
    _autopilot(config)["filters"]["schedules"] = ["flyInFlyOut"]
    settings = parse_autopilot_settings(config)
    with pytest.raises(AutopilotConfigError, match="filters.schedules"):
        validate_runtime_dependencies(settings, _runtime_context(config))


def test_runtime_preset_accepts_dotted_hh_industry_identifier() -> None:
    config = default_config()
    _autopilot(config)["accounts"][0]["resume_queries"] = [
        {"resume_id": "published:*", "preset_names": ["backend"]}
    ]
    context = _runtime_context(config)
    context.config["hh_campaign_presets"]["backend"]["industry"] = ["7.540"]

    expanded = validate_runtime_dependencies(
        parse_autopilot_settings(config), context
    )

    assert expanded["default"][0]["preset"]["industry"] == ["7.540"]


def test_validate_run_modes_enforce_their_authorization_boundaries() -> None:
    disabled = parse_autopilot_settings(default_config())
    with pytest.raises(AutopilotConfigError, match="enabled.*grant"):
        validate_run_mode(
            disabled,
            account_id="default",
            mode=ValidationMode.AUTONOMOUS,
            has_grant=True,
            literal_confirmed=False,
        )
    with pytest.raises(AutopilotConfigError, match="literal confirmation"):
        validate_run_mode(
            disabled,
            account_id="default",
            mode=ValidationMode.MANUAL,
            has_grant=True,
            literal_confirmed=False,
        )
    assert (
        validate_run_mode(
            disabled,
            account_id="default",
            mode=ValidationMode.SHADOW,
            has_grant=False,
            literal_confirmed=False,
        )
        is None
    )
    with pytest.raises(AutopilotConfigError, match="provenance"):
        validate_run_mode(
            disabled,
            account_id="default",
            mode=ValidationMode.RECOVERY,
            has_grant=False,
            literal_confirmed=False,
        )
    assert (
        validate_run_mode(
            disabled,
            account_id="default",
            mode=ValidationMode.RECOVERY,
            has_grant=False,
            literal_confirmed=False,
            has_recovery_provenance=True,
        )
        is AuthorizationKind.RECOVERY
    )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda config, material: _autopilot(config)["accounts"][0]["resume_queries"].append(
            {"resume_id": "r2", "preset_names": []}
        )
        or material,
        lambda config, material: replace(material, effective_auth_profile_id="work"),
        lambda config, material: material.candidate_profile.update({"desired_roles": ["go"]})
        or material,
        lambda config, material: replace(material, candidate_profile_version="profile-v2"),
        lambda config, material: material.resumes[0].update({"content_hash": "resume-v2"})
        or material,
        lambda config, material: material.presets["backend"].update({"text": "django"})
        or material,
        lambda config, material: _autopilot(config)["search"].update({"per_page": 99})
        or material,
        lambda config, material: _autopilot(config)["filters"].update(
            {"required_keywords": ["python"]}
        )
        or material,
        lambda config, material: _autopilot(config)["ranking"].update({"minimum_score": 61})
        or material,
        lambda config, material: replace(material, model_id="model-v2"),
        lambda config, material: material.prompt_versions.update({"ranking": "prompt-v2"})
        or material,
        lambda config, material: _autopilot(config)["limits"].update({"daily_success": 51})
        or material,
        lambda config, material: _autopilot(config).update({"timezone": "UTC"})
        or material,
        lambda config, material: _autopilot(config)["schedule"].update({"start": "09:00"})
        or material,
        lambda config, material: _autopilot(config)["limits"].update(
            {"send_delay_min_seconds": 46}
        )
        or material,
        lambda config, material: _autopilot(config)["retry"].update({"max_attempts": 5})
        or material,
        lambda config, material: replace(
            material, cover_letter_template_version="letter-v2"
        ),
        lambda config, material: _autopilot(config)["application"].update(
            {"cover_letter_mode": "ai"}
        )
        or material,
        lambda config, material: _autopilot(config)["application"].update(
            {"form_mode": "off"}
        )
        or material,
        lambda config, material: _autopilot(config)["application"].update(
            {"challenge_expiry_hours": 25}
        )
        or material,
        lambda config, material: _autopilot(config)["application"].update(
            {"captcha_mode": "manual_handoff"}
        )
        or material.transport_identity.update({"browser_proxy_id": "proxy-b"})
        or material,
        lambda config, material: _autopilot(config)["browser"].update(
            {"navigation_timeout_seconds": 31}
        )
        or material,
        lambda config, material: _autopilot(config)["limits"].update(
            {"administrative_max_daily_success": 199}
        )
        or material,
        lambda config, material: material.secret_versions.update({"access_token": 2})
        or material,
    ],
)
def test_every_authorization_policy_input_invalidates_hash(mutate) -> None:
    config = default_config()
    material = _material()
    before = policy_hash(parse_autopilot_settings(config), "default", material)
    material = mutate(config, material)
    after = policy_hash(parse_autopilot_settings(config), "default", material)
    assert before != after


def test_runtime_controls_do_not_widen_policy_hash() -> None:
    baseline = default_config()
    changed = deepcopy(baseline)
    account = _autopilot(changed)["accounts"][0]
    account.update({"enabled": True, "paused": True, "authorization_generation": 99})
    _autopilot(changed)["notifications"].update({"challenge": False, "run_failure": False})
    _autopilot(changed)["retention"].update(
        {"challenge_artifact_days": 99, "event_days": 999}
    )
    changed["log_level"] = "debug"
    assert policy_hash(
        parse_autopilot_settings(baseline), "default", _material()
    ) == policy_hash(parse_autopilot_settings(changed), "default", _material())


def test_semantically_unordered_arrays_do_not_change_hash() -> None:
    first = default_config()
    _autopilot(first)["filters"]["excluded_keywords"] = ["python", "1c"]
    _autopilot(first)["schedule"]["days"] = [1, 2, 3]
    second = deepcopy(first)
    _autopilot(second)["filters"]["excluded_keywords"].reverse()
    _autopilot(second)["schedule"]["days"].reverse()
    second_material = _material()
    second_material.resumes.reverse()
    assert policy_hash(
        parse_autopilot_settings(first), "default", _material()
    ) == policy_hash(parse_autopilot_settings(second), "default", second_material)


@pytest.mark.parametrize(
    "payload",
    [
        {"access_token": "raw"},
        {"api_key": "raw"},
        {"headers": {"Authorization": "Bearer raw"}},
        {"transport": {"cookies": ["raw"]}},
        {"proxy_password": "raw"},
    ],
)
def test_canonical_policy_rejects_raw_credentials(payload: dict) -> None:
    with pytest.raises(AutopilotConfigError, match="credential"):
        canonicalize_policy_payload(payload)


def test_secret_versions_accept_only_stable_positive_integer_ids() -> None:
    assert canonicalize_policy_payload({"secret_versions": {"access_token": 1}}) == {
        "secret_versions": {"access_token": 1}
    }
    with pytest.raises(AutopilotConfigError, match="secret_versions"):
        canonicalize_policy_payload({"secret_versions": {"access_token": "raw"}})
    with pytest.raises(AutopilotConfigError, match="credential"):
        policy_hash(
            parse_autopilot_settings(default_config()),
            "default",
            replace(_material(), transport_identity={"cookie": "raw"}),
        )


def test_masked_merge_preserves_managed_generation_and_rejects_user_override() -> None:
    stored = default_config()
    _autopilot(stored)["accounts"][0]["authorization_generation"] = 7
    submitted = {
        "sources": {
            "hh": {
                "autopilot": {
                    "accounts": [
                        {
                            "profile_id": "default",
                            "candidate_profile_id": "updated",
                            "enabled": False,
                            "paused": False,
                            "resume_queries": [
                                {"resume_id": "published:*", "preset_names": []}
                            ],
                        }
                    ]
                }
            }
        }
    }
    merged = merge_masked_config(stored, submitted)
    assert _autopilot(merged)["accounts"][0]["authorization_generation"] == 7

    round_trip = deepcopy(submitted)
    round_trip["sources"]["hh"]["autopilot"]["accounts"][0][
        "authorization_generation"
    ] = 7
    assert (
        _autopilot(merge_masked_config(stored, round_trip))["accounts"][0][
            "authorization_generation"
        ]
        == 7
    )

    submitted["sources"]["hh"]["autopilot"]["accounts"][0][
        "authorization_generation"
    ] = 8
    with pytest.raises(ValueError, match="service-managed"):
        merge_masked_config(stored, submitted)

    new_account = deepcopy(round_trip)
    new_account["sources"]["hh"]["autopilot"]["accounts"][0].update(
        {"profile_id": "new", "authorization_generation": None}
    )
    with pytest.raises(ValueError, match="service-managed"):
        merge_masked_config(stored, new_account)
