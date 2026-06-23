from __future__ import annotations

from typing import Any, Protocol


class CandidateFactStore(Protocol):
    def list_candidate_facts(self, *, profile_id: str, status: str | None = None) -> list[dict[str, Any]]:
        ...
