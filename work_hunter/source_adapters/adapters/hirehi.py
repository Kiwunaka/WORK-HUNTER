from __future__ import annotations

from ..base import RegisteredSourceAdapter
from ._registered import registered_adapter


class HirehiAdapter(RegisteredSourceAdapter):
    def __new__(cls):
        return registered_adapter("hirehi")
