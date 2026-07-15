from __future__ import annotations

from typing import Any, cast

from .hh_autopilot.types import LiteralConfirmation, LiveAuthorization


READ_ONLY_HTTP_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def is_literal_confirmation(value: object) -> bool:
    return value is True


def require_mutation_confirmation(
    confirm: object,
    *,
    code: str,
    message: str,
    risk_flags: tuple[str, ...],
    context: dict[str, object] | None = None,
) -> dict[str, Any] | None:
    if is_literal_confirmation(confirm):
        return None
    return {
        "status": "blocked",
        "code": code,
        "message": message,
        "requires_confirmation": True,
        "risk_flags": list(risk_flags),
        **(context or {}),
    }


def _hh_account_id(value: object) -> str:
    if not isinstance(value, str):
        raise PermissionError("HH account authorization mismatch")
    canonical = value.strip().casefold()
    if not canonical or "\0" in canonical:
        raise PermissionError("HH account authorization mismatch")
    return canonical


def require_hh_dispatch_authorization(
    authorization: LiteralConfirmation | LiveAuthorization,
    *,
    account_id: str,
    lease_account_id: str,
    fencing_token: int,
) -> None:
    if type(authorization) not in {LiteralConfirmation, LiveAuthorization}:
        raise PermissionError("typed HH application authorization required")
    requested_account = _hh_account_id(account_id)
    lease_account = _hh_account_id(lease_account_id)
    authorized_account = _hh_account_id(authorization.account_id)
    if authorized_account != requested_account or lease_account != requested_account:
        raise PermissionError("HH account authorization mismatch")
    if type(fencing_token) is not int or fencing_token < 0:
        raise PermissionError("invalid live HH application authorization")
    if type(authorization) is LiteralConfirmation:
        literal = cast(LiteralConfirmation, authorization)
        if (
            not isinstance(literal.reference_id, str)
            or not literal.reference_id.strip()
            or "\0" in literal.reference_id
        ):
            raise PermissionError("invalid literal HH application authorization")
        return
    live = cast(LiveAuthorization, authorization)
    if (
        live.scope != "applications"
        or type(live.fencing_token) is not int
        or live.fencing_token < 0
        or live.fencing_token != fencing_token
        or type(live.grant_id) is not int
        or live.grant_id < 1
        or type(live.run_id) is not int
        or live.run_id < 1
        or not isinstance(live.policy_hash, str)
        or not live.policy_hash.strip()
        or "\0" in live.policy_hash
    ):
        raise PermissionError("invalid live HH application authorization")
