from __future__ import annotations

import difflib


def replay_text_diff(before: str, after: str) -> str:
    return "\n".join(
        difflib.unified_diff(
            str(before or "").splitlines(),
            str(after or "").splitlines(),
            fromfile="before",
            tofile="after",
            lineterm="",
        )
    )
