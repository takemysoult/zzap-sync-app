"""Diagnostics + journal formatting (crash investigation): pure/no-raise checks."""
from __future__ import annotations

from app import diagnostics
from app.gui.screens.status import _fmt_dt


def test_sample_has_expected_keys_and_never_raises():
    s = diagnostics.sample()
    assert set(s) == {"rss_mb", "gdi", "user", "handles", "threads"}
    assert isinstance(diagnostics.format_sample(s), str)


def test_startup_guard_arm_and_disarm_are_safe(tmp_path, monkeypatch):
    # No log dir needed beyond tmp; arming + disarming must not raise.
    monkeypatch.setenv("ZZAP_APP_DATA", str(tmp_path))
    diagnostics.arm_startup_guard(timeout_s=3600)   # long: won't fire during the test
    diagnostics.disarm_startup_guard()              # cancels the pending dump


def test_fmt_dt_shows_date_and_time():
    assert _fmt_dt("2026-06-25T11:39:02") == "25.06.2026 11:39:02"
    assert _fmt_dt("") == ""
    assert _fmt_dt("не дата") == "не дата"          # unparseable passes through
