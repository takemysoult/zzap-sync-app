"""DPAPI secret round-trip. Skipped automatically if pywin32 (win32crypt) is absent."""
import pytest

pytest.importorskip("win32crypt")

from app.security.secrets import DpapiCipher, decrypt, encrypt  # noqa: E402


def test_dpapi_roundtrip_and_not_plaintext():
    cipher = DpapiCipher()
    secret = "ZZap1_секретный_ключ_тест_!@#$"
    blob = cipher.encrypt(secret)
    assert isinstance(blob, bytes)
    assert secret.encode("utf-8") not in blob   # not stored in plaintext
    assert cipher.decrypt(blob) == secret


def test_module_level_helpers_roundtrip():
    blob = encrypt("пароль_1С_123")
    assert decrypt(blob) == "пароль_1С_123"


def test_wrong_entropy_cannot_decrypt():
    good = DpapiCipher(entropy=b"salt-A")
    bad = DpapiCipher(entropy=b"salt-B")
    blob = good.encrypt("secret")
    with pytest.raises(Exception):
        bad.decrypt(blob)
