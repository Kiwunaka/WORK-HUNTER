import json
import os

import pytest
from cryptography.fernet import InvalidToken

from work_hunter.external_sessions import _load_sessions, sessions_file
from work_hunter.secret_store import seal, unseal


def test_secret_store_round_trip_and_tamper_rejection(tmp_path, monkeypatch):
    monkeypatch.setenv("WORK_HUNTER_VAULT_DIR", str(tmp_path / "vault"))
    original = {"cookie": "fixture-secret", "unicode": "Тест"}
    encrypted = seal(original)
    assert "fixture-secret" not in json.dumps(encrypted)
    assert unseal(encrypted) == original
    corrupted = {**encrypted, "ciphertext": "AAAA"}
    with pytest.raises((OSError, ValueError, InvalidToken)):
        unseal(corrupted)


def test_plaintext_legacy_session_is_upgraded_before_use(tmp_path, monkeypatch):
    monkeypatch.setenv("WORK_HUNTER_VAULT_DIR", str(tmp_path / "vault"))
    path = sessions_file(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    original = {"sessions": {"fixture": {"Cookie": "fixture-secret"}}}
    path.write_text(json.dumps(original), encoding="utf-8")
    assert _load_sessions(tmp_path) == original
    assert "fixture-secret" not in path.read_text("utf-8")
    assert _load_sessions(tmp_path) == original


@pytest.mark.skipif(os.name == "nt", reason="POSIX vault permissions")
def test_posix_vault_rejects_shared_directory(tmp_path, monkeypatch):
    directory = tmp_path / "vault"
    directory.mkdir(mode=0o755)
    directory.chmod(0o755)
    monkeypatch.setenv("WORK_HUNTER_VAULT_DIR", str(directory))
    with pytest.raises(PermissionError):
        seal({"cookie": "fixture"})
