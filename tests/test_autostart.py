"""AutostartManager behaviour with the registry mocked (dict-backed fake backend).

No real HKCU access — the manager logic (enable/disable/is_enabled, idempotency) is
tested against a fake backend, per the Phase 4 "registry mocked" requirement.
"""
from __future__ import annotations

from app.services.autostart import (APP_VALUE_NAME, MINIMIZED_FLAG, AutostartManager,
                                     launch_command)


class FakeBackend:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def get(self, name: str) -> str | None:
        return self.store.get(name)

    def set(self, name: str, value: str) -> None:
        self.store[name] = value

    def delete(self, name: str) -> None:
        self.store.pop(name, None)


def test_enable_sets_value_and_reports_enabled():
    backend = FakeBackend()
    mgr = AutostartManager(backend)
    assert mgr.is_enabled() is False
    mgr.enable("MY_COMMAND")
    assert mgr.is_enabled() is True
    assert backend.store[APP_VALUE_NAME] == "MY_COMMAND"


def test_disable_removes_value_and_is_idempotent():
    backend = FakeBackend()
    mgr = AutostartManager(backend)
    mgr.disable()                       # absent -> no error
    mgr.enable("X")
    mgr.disable()
    assert mgr.is_enabled() is False
    mgr.disable()                       # idempotent
    assert mgr.is_enabled() is False


def test_apply_toggles_state():
    backend = FakeBackend()
    mgr = AutostartManager(backend)
    mgr.apply(True, "CMD")
    assert mgr.is_enabled() is True
    mgr.apply(False)
    assert mgr.is_enabled() is False


def test_enable_uses_default_command_when_none_given():
    backend = FakeBackend()
    mgr = AutostartManager(backend)
    mgr.enable()
    assert MINIMIZED_FLAG in backend.store[APP_VALUE_NAME]


def test_launch_command_minimized_flag():
    assert MINIMIZED_FLAG in launch_command(minimized=True)
    assert MINIMIZED_FLAG not in launch_command(minimized=False)
