from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "telegram-chat-retrospective-2026-07-11-13.html"
SOURCE = Path(
    r"C:\Users\kiwun\Downloads\Telegram Desktop\ChatExport_2026-07-13 (1)\result.json"
)
SOURCE_SHA256 = "BE65A707D8AC48A775998D745A91D3400720EB36996226F0AB4DD09494BCC0BF"
REQUIRED_SECTIONS = ("overview", "story", "ledger", "polls", "jokes", "gems", "method")


def _source() -> dict:
    return json.loads(SOURCE.read_text(encoding="utf-8"))


def _html() -> str:
    return ARTIFACT.read_text(encoding="utf-8")


def test_authoritative_source_snapshot_is_unchanged():
    raw = SOURCE.read_bytes()
    assert len(raw) == 2_085_892
    assert hashlib.sha256(raw).hexdigest().upper() == SOURCE_SHA256


def test_offline_document_shell_exists():
    html = _html()
    assert '<html lang="ru">' in html
    assert '<meta http-equiv="Content-Security-Policy"' in html
    assert "default-src 'none'" in html
    assert "connect-src 'none'" in html
    assert "font-src 'none'" in html
    assert "<main" in html and "</main>" in html
    for section_id in REQUIRED_SECTIONS:
        assert re.search(rf'<section[^>]+id="{section_id}"', html)
