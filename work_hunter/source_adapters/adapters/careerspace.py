from __future__ import annotations

from ..base import RegisteredSourceAdapter
from ._registered import registered_adapter


class CareerspaceAdapter(RegisteredSourceAdapter):
    def __new__(cls):
        return registered_adapter("careerspace")
