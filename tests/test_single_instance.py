"""Single-instance guard (Phase 6) — a second guard on the same key isn't primary."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide2")

from PySide2.QtWidgets import QApplication  # noqa: E402

from app.single_instance import SingleInstance  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_first_is_primary_second_is_not(qapp):
    first = SingleInstance("ZZapSyncTest_singleton")
    assert first.is_primary() is True
    second = SingleInstance("ZZapSyncTest_singleton")
    assert second.is_primary() is False     # the segment is already held by `first`
