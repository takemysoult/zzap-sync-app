"""Watchdog (Phase 6): liveness decision + relaunch/notify, all mocked (no real GUI)."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app import paths
from app.watchdog import app_is_down, run_watchdog, write_heartbeat


def test_app_is_down_truth_table():
    now = datetime(2026, 6, 24, 12, 0, 0)
    assert app_is_down(None, now) is True                       # never wrote
    assert app_is_down(now - timedelta(minutes=10), now) is True  # stale
    assert app_is_down(now - timedelta(minutes=1), now) is False  # fresh


@pytest.fixture
def appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("ZZAP_APP_DATA", str(tmp_path))
    return tmp_path


def test_run_watchdog_noop_when_app_alive(appdata):
    write_heartbeat()                       # fresh heartbeat = app running
    calls = []
    rc = run_watchdog(relaunch=lambda: calls.append("relaunch"),
                      notify=lambda t: calls.append("notify"))
    assert rc == 0
    assert calls == []                      # didn't touch a healthy app


def test_run_watchdog_relaunches_and_notifies_when_down(appdata):
    calls = []                              # no heartbeat written → app is down
    rc = run_watchdog(relaunch=lambda: calls.append("relaunch"),
                      notify=lambda t: calls.append("notify"))
    assert rc == 1
    assert "relaunch" in calls and "notify" in calls


def test_run_watchdog_disabled_does_nothing(appdata):
    from app.db.dal import Database
    from app.services.watchdog_task import SETTING_WATCHDOG_ENABLED
    db = Database(paths.db_path())
    db.set_bool(SETTING_WATCHDOG_ENABLED, False)
    db.close()
    calls = []
    rc = run_watchdog(relaunch=lambda: calls.append("relaunch"),
                      notify=lambda t: calls.append("notify"))
    assert rc == 0
    assert calls == []
