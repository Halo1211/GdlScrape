"""OS-protected secret storage used by account profiles."""

from __future__ import annotations

import base64
import ctypes
import json
import os
import uuid
from ctypes import wintypes
from pathlib import Path

from .core import APP_DIR, atomic_write_text, read_text_safely


VAULT_FILE = APP_DIR / "secure_secrets.json"
SERVICE_NAME = "Gallery-DL-GUI"


class SecretVault:
    """Prefer system keyring and fall back to Windows DPAPI.

    On systems without either backend, storing a new secret is refused rather
    than silently writing plaintext.
    """

    def __init__(self, path: str | Path = VAULT_FILE) -> None:
        self.path = Path(path)
        self._keyring = None
        try:
            import keyring  # type: ignore

            backend = keyring.get_keyring()
            if float(getattr(backend, "priority", 0) or 0) > 0:
                self._keyring = keyring
        except Exception:
            self._keyring = None

    @property
    def backend_name(self) -> str:
        if self._keyring is not None:
            return "System keyring"
        if os.name == "nt":
            return "Windows DPAPI"
        return "Unavailable"

    @property
    def available(self) -> bool:
        return self._keyring is not None or os.name == "nt"

    def new_reference(self) -> str:
        return f"account:{uuid.uuid4().hex}"

    def set(self, reference: str, secret: str) -> None:
        if not reference:
            raise ValueError("Secret reference is required")
        if self._keyring is not None:
            self._keyring.set_password(SERVICE_NAME, reference, secret)
            return
        if os.name != "nt":
            raise RuntimeError("Install the optional 'keyring' package to store secrets securely")
        values = self._read_dpapi_values(strict=True)
        values[reference] = base64.b64encode(self._dpapi_protect(secret.encode("utf-8"))).decode("ascii")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self.path, json.dumps(values, indent=2), encoding="utf-8")
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def get(self, reference: str) -> str | None:
        if not reference:
            return None
        if self._keyring is not None:
            return self._keyring.get_password(SERVICE_NAME, reference)
        if os.name != "nt":
            return None
        encoded = self._read_dpapi_values(strict=True).get(reference)
        if not encoded:
            return None
        return self._dpapi_unprotect(base64.b64decode(encoded)).decode("utf-8")

    def delete(self, reference: str) -> None:
        if not reference:
            return
        if self._keyring is not None:
            # Deleting a reference that is already absent is idempotent, but a
            # real keyring/backend failure must reach the caller. Management's
            # account UI can then warn that profile metadata was removed while
            # the OS-stored secret still needs cleanup.
            if self._keyring.get_password(SERVICE_NAME, reference) is None:
                return
            self._keyring.delete_password(SERVICE_NAME, reference)
            return
        if os.name != "nt":
            return
        values = self._read_dpapi_values(strict=True)
        if reference in values:
            del values[reference]
            atomic_write_text(self.path, json.dumps(values, indent=2), encoding="utf-8")

    def _read_dpapi_values(self, *, strict: bool = False) -> dict[str, str]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(read_text_safely(self.path))
        except (OSError, ValueError) as exc:
            if strict:
                raise RuntimeError(
                    f"Secure secret vault is unreadable; the existing file was preserved: {exc}"
                ) from exc
            return {}
        if not isinstance(data, dict):
            if strict:
                raise RuntimeError(
                    "Secure secret vault must contain a JSON object; the existing file was preserved"
                )
            return {}
        return {str(key): str(value) for key, value in data.items()}

    @staticmethod
    def _dpapi_protect(data: bytes) -> bytes:
        return _crypt_protect(data, decrypt=False)

    @staticmethod
    def _dpapi_unprotect(data: bytes) -> bytes:
        return _crypt_protect(data, decrypt=True)


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _crypt_protect(data: bytes, *, decrypt: bool) -> bytes:
    if os.name != "nt":
        raise RuntimeError("DPAPI is only available on Windows")
    buffer = ctypes.create_string_buffer(data)
    source = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    destination = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    function = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    if decrypt:
        ok = function(ctypes.byref(source), None, None, None, None, 0, ctypes.byref(destination))
    else:
        ok = function(ctypes.byref(source), None, None, None, None, 0, ctypes.byref(destination))
    if not ok:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(destination.pbData, destination.cbData)
    finally:
        local_free = ctypes.windll.kernel32.LocalFree
        local_free.argtypes = [ctypes.c_void_p]
        local_free.restype = ctypes.c_void_p
        local_free(ctypes.cast(destination.pbData, ctypes.c_void_p))


__all__ = ["SecretVault", "VAULT_FILE"]
