"""Shared test fixtures/fakes.

`engine` and `app` are importable because pyproject sets pytest `pythonpath = ["."]`.
"""
from __future__ import annotations

from engine.models import PriceRow


class FakeCipher:
    """Reversible, deterministic, non-identity cipher for DAL tests (no DPAPI needed).

    Lets DAL tests assert encryption-at-rest (stored blob != plaintext) and round-trip
    without depending on the platform's DPAPI.
    """
    _KEY = 0x5A

    def encrypt(self, plaintext: str) -> bytes:
        return b"ENC:" + bytes(b ^ self._KEY for b in plaintext.encode("utf-8"))

    def decrypt(self, blob: bytes) -> str:
        blob = bytes(blob)
        assert blob[:4] == b"ENC:", "blob not produced by FakeCipher"
        return bytes(b ^ self._KEY for b in blob[4:]).decode("utf-8")


def make_rows() -> list[PriceRow]:
    return [
        PriceRow("BrandA", "A-1", "Деталь 1", 5, 100.0),
        PriceRow("BrandB", "B-2", "Деталь 2", 2, 50.5),
        PriceRow("", "", "Без номера", 1, 9.9),  # dropped by clean_rows
    ]
