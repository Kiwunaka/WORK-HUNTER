from __future__ import annotations

from typing import Any


class HHTransportError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str = "hh_transport_error",
        payload: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.payload = payload or {}


class HHNetworkError(HHTransportError):
    pass


class HHParseError(HHTransportError):
    pass


class HHAuthError(HHTransportError):
    pass


class HHForbiddenError(HHTransportError):
    pass


class HHRateLimitError(HHTransportError):
    pass


class HHValidationError(HHTransportError):
    pass
