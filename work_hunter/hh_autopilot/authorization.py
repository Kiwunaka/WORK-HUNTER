from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from work_hunter.config import _update_autopilot_authorization_projection

from .config import (
    AccountSettings,
    AutopilotSettings,
    PolicyMaterial,
    parse_autopilot_settings,
    policy_hash,
)
from .repository import (
    AutopilotRepository,
    KillSwitchActive,
    RepositoryAuthorizationDenied,
)
from .types import LiveAuthorization


PolicyMaterialResolver = Callable[[dict[str, Any], str], PolicyMaterial]


class AuthorizationDenied(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class EnableResult:
    """Result record with detached, intentionally caller-mutable mapping snapshots."""

    config: dict[str, Any]
    generations: dict[str, int]
    policy_hashes: dict[str, str]


@dataclass(frozen=True)
class AuthorizationMutationResult:
    config: dict[str, Any]


@dataclass(frozen=True)
class ReconciliationResult:
    """Detached reconciliation snapshot; mutating it cannot change repository state."""

    mismatches: dict[str, str]
    revoked_accounts: tuple[str, ...]


def _canonical_account_id(value: Any, *, field: str = "account_id") -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string")
    canonical = value.strip().casefold()
    if not canonical:
        raise ValueError(f"{field} must not be empty")
    if "\0" in canonical:
        raise ValueError(f"{field} must not contain NUL")
    return canonical


def _positive_integer(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field} must be an integer")
    if value < 1:
        raise ValueError(f"{field} must be positive")
    return value


def _fencing_token(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("fencing_token must be an integer")
    if value < 0:
        raise ValueError("fencing_token must be non-negative")
    return value


def _required_text(value: Any, *, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field} must not be empty")
    return normalized


class HHAutopilotAuthorizer:
    def __init__(
        self,
        repository: AutopilotRepository,
        config_path: str | Path | None = None,
        *,
        policy_material_resolver: PolicyMaterialResolver | None = None,
    ) -> None:
        if not isinstance(repository, AutopilotRepository):
            raise TypeError("repository must be an AutopilotRepository")
        self.repository = repository
        self.config_path = None if config_path is None else Path(config_path)
        self.policy_material_resolver = policy_material_resolver

    def enable(
        self,
        account_ids: list[str],
        config: dict[str, Any],
        *,
        confirm: object,
        actor: str,
        source: str,
    ) -> EnableResult:
        self._require_confirmation(confirm)
        actor = _required_text(actor, field="actor")
        source = _required_text(source, field="source")
        settings, selected = self._selected_accounts(config, account_ids)
        for account in selected:
            if self.repository.kill_switch_active(account.profile_id):
                raise AuthorizationDenied("kill_switch_active")
        hashes = {
            self._account_key(account): self._policy_hash(
                config, settings, account.profile_id
            )
            for account in selected
        }
        requests = [
            (account_id, hashes[account_id], actor, source)
            for account_id in hashes
        ]
        try:
            generations = self.repository.create_grants(requests)
        except KillSwitchActive as exc:
            raise AuthorizationDenied("kill_switch_active") from exc
        try:
            projected = self._write_projection(
                config, generations, enabled=True
            )
            fresh_settings = parse_autopilot_settings(projected)
            for account_id, expected_hash in hashes.items():
                active_grant = self.repository.active_grant(
                    account_id, "applications"
                )
                if (
                    active_grant is None
                    or active_grant.generation != generations[account_id]
                ):
                    raise AuthorizationDenied("authorization_state_mismatch")
                if active_grant.policy_hash != expected_hash:
                    raise AuthorizationDenied("policy_hash_mismatch")
                fresh_account = self._find_account(fresh_settings, account_id)
                if (
                    not fresh_account.enabled
                    or fresh_account.authorization_generation
                    != generations[account_id]
                ):
                    raise AuthorizationDenied("authorization_state_mismatch")
                if (
                    self._policy_hash(
                        projected,
                        fresh_settings,
                        fresh_account.profile_id,
                    )
                    != expected_hash
                ):
                    raise AuthorizationDenied("policy_hash_mismatch")
        except BaseException:
            self.repository.revoke_exact_generations(
                generations,
                actor=actor,
                reason="projection_write_failed",
            )
            raise
        return EnableResult(
            config=projected,
            generations=dict(generations),
            policy_hashes=dict(hashes),
        )

    def disable(
        self,
        account_ids: list[str],
        config: dict[str, Any],
        *,
        confirm: object,
        actor: str,
    ) -> AuthorizationMutationResult:
        self._require_confirmation(confirm)
        actor = _required_text(actor, field="actor")
        _, selected = self._selected_accounts(config, account_ids)
        selected_ids = [self._account_key(account) for account in selected]
        self.repository.disable_accounts(
            selected_ids, actor=actor, reason="disabled"
        )
        projected = self._write_projection(
            config,
            {account_id: None for account_id in selected_ids},
            enabled=False,
        )
        return AuthorizationMutationResult(config=projected)

    def reconcile_config_projection(
        self,
        config: dict[str, Any],
        *,
        actor: str,
    ) -> ReconciliationResult:
        actor = _required_text(actor, field="actor")
        settings = parse_autopilot_settings(config)
        projections = {
            self._account_key(account): (
                account.enabled,
                account.authorization_generation,
            )
            for account in settings.accounts
        }
        policy_hashes: dict[str, str] = {}
        policy_errors: dict[str, str] = {}
        for account in settings.accounts:
            account_id = self._account_key(account)
            if not account.enabled:
                continue
            try:
                policy_hashes[account_id] = self._policy_hash(
                    config, settings, account.profile_id
                )
            except AuthorizationDenied as exc:
                policy_errors[account_id] = exc.code
        reconciled = self.repository.reconcile_authorization_projection(
            projections,
            actor=actor,
            reason="config_projection_reconciliation",
            policy_hashes=policy_hashes,
            policy_errors=policy_errors,
        )
        return ReconciliationResult(
            mismatches=dict(reconciled.mismatches),
            revoked_accounts=reconciled.revoked_accounts,
        )

    def issue_live_authorization(
        self,
        account_id: str,
        config: dict[str, Any],
        *,
        run_id: int,
        fencing_token: int,
    ) -> LiveAuthorization:
        canonical_account = _canonical_account_id(account_id)
        run_id = _positive_integer(run_id, field="run_id")
        fencing_token = _fencing_token(fencing_token)
        settings = parse_autopilot_settings(config)
        account = self._find_account(settings, canonical_account)
        if account.authorization_generation is None:
            raise AuthorizationDenied("authorization_state_mismatch")
        if not account.enabled or account.paused:
            raise AuthorizationDenied("autopilot_disabled_or_paused")
        current_hash = self._policy_hash(
            config, settings, account.profile_id
        )
        try:
            snapshot = self.repository.validate_live_authorization_snapshot(
                canonical_account,
                generation=account.authorization_generation,
                policy_hash=current_hash,
                run_id=run_id,
                fencing_token=fencing_token,
            )
        except RepositoryAuthorizationDenied as exc:
            raise AuthorizationDenied(exc.code) from exc
        return LiveAuthorization(
            grant_id=snapshot.grant.id,
            scope="applications",
            account_id=canonical_account,
            run_id=snapshot.run.id,
            fencing_token=fencing_token,
            policy_hash=snapshot.grant.policy_hash,
        )

    def set_pause(
        self,
        scope_type: str,
        scope_id: str,
        config: dict[str, Any],
        *,
        paused: bool,
        confirm: object,
        actor: str,
    ) -> AuthorizationMutationResult:
        self._require_confirmation(confirm)
        _required_text(actor, field="actor")
        if type(paused) is not bool:
            raise TypeError("paused must be a boolean")
        settings = parse_autopilot_settings(config)
        normalized_type, normalized_id, selected_ids = self._control_selection(
            settings, scope_type, scope_id
        )
        self.repository.set_pause(normalized_type, normalized_id, paused)
        if normalized_type == "account":
            projected = self._write_field_projection(
                config,
                {selected_ids[0]: {"paused": paused}},
            )
        else:
            projected = copy.deepcopy(config)
        return AuthorizationMutationResult(config=projected)

    def set_kill_switch(
        self,
        scope_type: str,
        scope_id: str,
        config: dict[str, Any],
        *,
        confirm: object,
        actor: str,
    ) -> AuthorizationMutationResult:
        self._require_confirmation(confirm)
        actor = _required_text(actor, field="actor")
        settings = parse_autopilot_settings(config)
        normalized_type, normalized_id, selected_ids = self._control_selection(
            settings, scope_type, scope_id
        )
        self.repository.set_kill_switch(
            normalized_type, normalized_id, actor=actor
        )
        projected = self._write_field_projection(
            config,
            {
                account_id: {
                    "enabled": False,
                    "authorization_generation": None,
                }
                for account_id in selected_ids
            },
        )
        return AuthorizationMutationResult(config=projected)

    def clear_kill_switch(
        self,
        scope_type: str,
        scope_id: str,
        config: dict[str, Any],
        *,
        confirm: object,
        actor: str,
    ) -> AuthorizationMutationResult:
        self._require_confirmation(confirm)
        actor = _required_text(actor, field="actor")
        settings = parse_autopilot_settings(config)
        normalized_type, normalized_id, _ = self._control_selection(
            settings, scope_type, scope_id
        )
        self.repository.clear_kill_switch(
            normalized_type, normalized_id, actor=actor
        )
        return AuthorizationMutationResult(config=copy.deepcopy(config))

    def _selected_accounts(
        self, config: dict[str, Any], account_ids: list[str]
    ) -> tuple[AutopilotSettings, tuple[AccountSettings, ...]]:
        settings = parse_autopilot_settings(config)
        if isinstance(account_ids, (str, bytes)) or not isinstance(
            account_ids, (list, tuple)
        ):
            raise TypeError("account_ids must be a list")
        selected: list[AccountSettings] = []
        seen: set[str] = set()
        for index, raw_account_id in enumerate(account_ids):
            account_id = _canonical_account_id(
                raw_account_id, field=f"account_ids[{index}]"
            )
            if account_id in seen:
                raise ValueError("account_ids contains a duplicate account identifier")
            seen.add(account_id)
            selected.append(self._find_account(settings, account_id))
        if not selected:
            raise ValueError("account_ids must not be empty")
        return settings, tuple(selected)

    @staticmethod
    def _find_account(
        settings: AutopilotSettings, account_id: str
    ) -> AccountSettings:
        for account in settings.accounts:
            if account.profile_id.strip().casefold() == account_id:
                return account
        raise AuthorizationDenied("unknown_account")

    @staticmethod
    def _account_key(account: AccountSettings) -> str:
        return _canonical_account_id(account.profile_id)

    def _policy_hash(
        self,
        config: dict[str, Any],
        settings: AutopilotSettings,
        account_id: str,
    ) -> str:
        resolver = self.policy_material_resolver
        if resolver is None:
            raise AuthorizationDenied("policy_material_unavailable")
        try:
            material = resolver(copy.deepcopy(config), account_id)
            if not isinstance(material, PolicyMaterial):
                raise TypeError("policy material resolver returned an invalid value")
            return policy_hash(settings, account_id, material)
        except AuthorizationDenied:
            raise
        except Exception as exc:
            raise AuthorizationDenied("policy_material_unavailable") from exc

    def _write_projection(
        self,
        config: dict[str, Any],
        generations: Mapping[str, int | None],
        *,
        enabled: bool,
    ) -> dict[str, Any]:
        return self._write_field_projection(
            config,
            {
                account_id: {
                    "enabled": enabled,
                    "authorization_generation": generation,
                }
                for account_id, generation in generations.items()
            },
        )

    def _write_field_projection(
        self,
        config: dict[str, Any],
        projections: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        if self.config_path is None:
            raise AuthorizationDenied("config_projection_path_required")
        return _update_autopilot_authorization_projection(
            self.config_path,
            config,
            projections,
        )

    def _control_selection(
        self,
        settings: AutopilotSettings,
        scope_type: str,
        scope_id: str,
    ) -> tuple[str, str, list[str]]:
        normalized_type = _required_text(
            scope_type, field="scope_type"
        ).casefold()
        if normalized_type == "global":
            normalized_scope_id = _required_text(
                scope_id, field="scope_id"
            ).casefold()
            if normalized_scope_id not in {"global", "*"}:
                raise ValueError("global control scope_id must be global")
            return (
                "global",
                "global",
                [self._account_key(account) for account in settings.accounts],
            )
        if normalized_type != "account":
            raise ValueError("scope_type must be global or account")
        account_id = _canonical_account_id(scope_id, field="scope_id")
        self._find_account(settings, account_id)
        return "account", account_id, [account_id]

    @staticmethod
    def _require_confirmation(confirm: object) -> None:
        if confirm is not True:
            raise AuthorizationDenied("literal_confirmation_required")


__all__ = [
    "AuthorizationDenied",
    "AuthorizationMutationResult",
    "EnableResult",
    "HHAutopilotAuthorizer",
    "PolicyMaterialResolver",
    "ReconciliationResult",
]
