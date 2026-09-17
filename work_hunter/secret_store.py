"""Encrypted local secrets: Windows user DPAPI or a private per-user POSIX key."""
from __future__ import annotations

import base64
import ctypes
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, ClassVar

from cryptography.fernet import Fernet


class _Blob(ctypes.Structure):
    _fields_: ClassVar = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _dpapi(data: bytes, *, decrypt: bool = False) -> bytes:
    if sys.platform != "win32":
        raise OSError("DPAPI is available on Windows only")
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(_Blob)]
    function.restype = ctypes.c_int
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    input_blob = _Blob(len(data), buffer)
    output_blob = _Blob()
    # CRYPTPROTECT_UI_FORBIDDEN; never use LOCAL_MACHINE (other users could decrypt).
    if not function(ctypes.byref(input_blob), None, None, None, None, 1, ctypes.byref(output_blob)):
        raise OSError("Windows credential protection failed")
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        ctypes.memset(output_blob.pbData, 0, output_blob.cbData)
        kernel.LocalFree(output_blob.pbData)


def _user_key() -> bytes:
    if sys.platform == "win32":
        raise OSError("Windows uses DPAPI instead of a file key")
    directory = Path(os.environ.get("WORK_HUNTER_VAULT_DIR") or Path.home() / ".work-hunter-vault")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.is_symlink() or directory.stat().st_uid != os.getuid() or directory.stat().st_mode & 0o077:
        raise PermissionError("Credential vault must be owned by this user with mode 0700")
    path = directory / "key"
    if not path.exists():
        # Publish a complete key atomically, without overwriting a competing creator.
        descriptor, filename = tempfile.mkstemp(dir=directory)
        temporary = Path(filename)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(Fernet.generate_key())
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, path)
            except FileExistsError:
                pass
        finally:
            temporary.unlink(missing_ok=True)
    if path.is_symlink() or path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o077:
        raise PermissionError("Credential key must be private to this user")
    return path.read_bytes()


def seal(value: dict[str, Any]) -> dict[str, str]:
    data = json.dumps(value, ensure_ascii=False).encode("utf-8")
    if os.name == "nt":
        return {"protection": "dpapi-user-v1", "ciphertext": base64.b64encode(_dpapi(data)).decode("ascii")}
    return {"protection": "fernet-user-v1", "ciphertext": Fernet(_user_key()).encrypt(data).decode("ascii")}


def unseal(value: dict[str, Any]) -> dict[str, Any]:
    if value.get("protection") == "dpapi-user-v1" and os.name == "nt":
        data = _dpapi(base64.b64decode(value["ciphertext"], validate=True), decrypt=True)
    elif value.get("protection") == "fernet-user-v1" and os.name != "nt":
        data = Fernet(_user_key()).decrypt(value["ciphertext"].encode("ascii"))
    else:
        raise ValueError("Credential store belongs to another operating system or has an unknown format")
    result = json.loads(data)
    if not isinstance(result, dict):
        raise TypeError("Invalid credential store")
    return result
