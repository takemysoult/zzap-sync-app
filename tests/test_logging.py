"""setup_logging: a rotating file handler is attached and the log file is written.

Isolated to a temp ZZAP_APP_DATA; root logging handlers are restored in teardown so
this never pollutes other tests.
"""
from __future__ import annotations

import logging

import pytest

from app.logging_setup import _FILE_TAG, setup_logging


@pytest.fixture
def isolated_logging(tmp_path, monkeypatch):
    monkeypatch.setenv("ZZAP_APP_DATA", str(tmp_path))
    root = logging.getLogger()
    saved = root.handlers[:]
    saved_level = root.level
    root.handlers = []
    try:
        yield tmp_path
    finally:
        for h in root.handlers:
            h.close()
        root.handlers = saved
        root.setLevel(saved_level)


def _file_handlers(root):
    return [h for h in root.handlers if getattr(h, "_zzap_tag", None) == _FILE_TAG]


def test_setup_logging_writes_a_rotating_file(isolated_logging):
    tmp_path = isolated_logging
    setup_logging()
    logging.getLogger("test").info("привет журнал")  # Cyrillic must survive (utf-8)

    log_file = tmp_path / "logs" / "app.log"
    assert log_file.exists()
    assert "привет журнал" in log_file.read_text(encoding="utf-8")


def test_setup_logging_is_idempotent(isolated_logging):
    setup_logging()
    setup_logging()
    root = logging.getLogger()
    assert len(_file_handlers(root)) == 1     # not duplicated on a second call
