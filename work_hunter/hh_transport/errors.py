from __future__ import annotations

from typing import Any

from work_hunter.hh_autopilot.types import DeliveryCertainty


class HHTransportError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str = "hh_transport_error",
        payload: dict[str, Any] | None = None,
        delivery_certainty: DeliveryCertainty = DeliveryCertainty.DEFINITE_RESPONSE,
    ):
        super().__init__(message)
        if not isinstance(delivery_certainty, DeliveryCertainty):
            raise TypeError("delivery_certainty must be DeliveryCertainty")
        self.status_code = status_code
        self.code = code
        self.payload = payload or {}
        self.delivery_certainty = delivery_certainty


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
