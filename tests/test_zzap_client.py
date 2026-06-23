"""ZZap upload payload correctness (the production-verified contract, PROJECT_MEMORY §4).

Network is mocked via an injected fake session; the test asserts the exact payload
and headers so a future refactor cannot silently break the working contract.
"""
import base64

import pytest
import requests

from engine.config import DEFAULT_ZZAP_API_URL, ZzapConfig
from engine.zzap_client import upload_price


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data
        self.text = text

    def json(self):
        if self._json is None:
            raise ValueError("no json body")
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def post(self, url, *, json, headers, timeout):
        self.calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        return self.response


def _cfg():
    return ZzapConfig(api_key="zzap1_test_key", code_templ=330017019)


def _xlsx(tmp_path, data=b"BINARY-XLSX-BYTES"):
    f = tmp_path / "price.xlsx"
    f.write_bytes(data)
    return f


def test_upload_sends_exact_payload(tmp_path):
    f = _xlsx(tmp_path)
    sess = FakeSession(FakeResponse(200, {"success": True, "code": 200, "errors": {}}))

    data = upload_price(_cfg(), f, session=sess)

    assert data["success"] is True
    assert len(sess.calls) == 1
    call = sess.calls[0]
    assert call["url"] == DEFAULT_ZZAP_API_URL
    assert call["headers"] == {"zzap-api-key": "zzap1_test_key"}
    body = call["json"]
    # The non-negotiable bits: 0-indexed single part + correct template + base64 file.
    assert body["part_num"] == 0
    assert body["part_total"] == 1
    assert body["code_templ"] == 330017019
    assert body["file_name"] == "price.xlsx"
    assert base64.b64decode(body["file_body"]) == b"BINARY-XLSX-BYTES"


def test_upload_400_raises(tmp_path):
    sess = FakeSession(FakeResponse(400, text="Неверное значение part_total"))
    with pytest.raises(RuntimeError, match="400"):
        upload_price(_cfg(), _xlsx(tmp_path), session=sess)


def test_upload_401_raises(tmp_path):
    sess = FakeSession(FakeResponse(401))
    with pytest.raises(RuntimeError, match="401"):
        upload_price(_cfg(), _xlsx(tmp_path), session=sess)


def test_upload_success_false_raises(tmp_path):
    sess = FakeSession(FakeResponse(200, {"success": False, "errors": {"x": "bad"}}))
    with pytest.raises(RuntimeError, match="ошибк"):
        upload_price(_cfg(), _xlsx(tmp_path), session=sess)
