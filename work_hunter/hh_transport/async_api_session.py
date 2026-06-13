from __future__ import annotations

import asyncio
import time
from typing import Any

from .api_session import HHApiSession
from .backends import ConfigBackend


class HHAsyncApiSession:
    def __init__(
        self,
        config: dict[str, Any],
        *,
        backend: ConfigBackend | None = None,
        min_interval_seconds: float = 0.0,
    ):
        self._session = HHApiSession(config, backend=backend)
        self._min_interval_seconds = min_interval_seconds
        self._rate_lock = asyncio.Lock()
        self._refresh_lock = asyncio.Lock()
        self._last_request_at = 0.0

    @property
    def identity(self):
        return self._session.identity

    async def request_json(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        await self._reserve_rate_slot()
        return await asyncio.to_thread(self._session.request_json, method, path, **kwargs)

    async def refresh_token(self) -> dict[str, Any]:
        async with self._refresh_lock:
            return await asyncio.to_thread(self._session.refresh_token)

    async def aclose(self) -> None:
        return None

    async def _reserve_rate_slot(self) -> None:
        if self._min_interval_seconds <= 0:
            return
        async with self._rate_lock:
            now = time.monotonic()
            wait_for = self._min_interval_seconds - (now - self._last_request_at)
            if wait_for > 0:
                await asyncio.sleep(wait_for)
            self._last_request_at = time.monotonic()
