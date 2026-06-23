"""Encrypted-at-rest secrets via Windows DPAPI (per-user).

DPAPI (`CryptProtectData`/`CryptUnprotectData`) ties the ciphertext to the current
Windows user account: only the same user on the same machine can decrypt it, and no
key material is stored by us. We persist the returned opaque blob (BLOB) in SQLite.

The `Cipher` protocol lets callers (and tests) inject an alternative implementation;
the DAL depends on the protocol, not on win32crypt, so DAL tests run without DPAPI.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

# Stored in the DPAPI blob's description field; purely informational.
_DESCRIPTION = "zzap-sync-app secret"


@runtime_checkable
class Cipher(Protocol):
    def encrypt(self, plaintext: str) -> bytes: ...
    def decrypt(self, blob: bytes) -> str: ...


class DpapiCipher:
    """Windows DPAPI-backed cipher (CryptProtectData / CryptUnprotectData).

    `entropy` is optional extra secret bytes mixed into protect/unprotect; if used,
    the exact same value is required to decrypt. We default to None (user+machine
    scope is the protection boundary).
    """

    def __init__(self, entropy: bytes | None = None) -> None:
        self._entropy = entropy

    def encrypt(self, plaintext: str) -> bytes:
        win32crypt = _import_win32crypt()
        blob = win32crypt.CryptProtectData(
            plaintext.encode("utf-8"), _DESCRIPTION, self._entropy, None, None, 0)
        return bytes(blob)

    def decrypt(self, blob: bytes) -> str:
        win32crypt = _import_win32crypt()
        _desc, data = win32crypt.CryptUnprotectData(
            bytes(blob), self._entropy, None, None, 0)
        return bytes(data).decode("utf-8")


def _import_win32crypt():
    try:
        import win32crypt
    except ImportError as e:  # pragma: no cover - зависит от окружения
        raise RuntimeError(
            "DPAPI требует pywin32 (Windows). Установите: pip install pywin32"
        ) from e
    return win32crypt


# Module-level convenience using a default DPAPI cipher.
_default = DpapiCipher()


def encrypt(plaintext: str) -> bytes:
    return _default.encrypt(plaintext)


def decrypt(blob: bytes) -> str:
    return _default.decrypt(blob)
