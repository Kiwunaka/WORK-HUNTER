from __future__ import annotations

from ..registry import source_adapter_registry


def registered_adapter(name: str):
    return source_adapter_registry()[name]
