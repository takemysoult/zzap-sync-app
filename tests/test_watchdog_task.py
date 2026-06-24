"""Watchdog scheduled-task management (Phase 6) — schtasks runner injected (not real)."""
from __future__ import annotations

from app.services import watchdog_task
from app.services.watchdog_task import (TASK_NAME, WATCHDOG_FLAG, build_create_args,
                                        watchdog_command)


def test_build_create_args_carries_interval_and_command():
    args = build_create_args(7, '"C:\\x\\ZZapSync.exe" --watchdog')
    assert "schtasks" == args[0] and "/create" in args
    assert TASK_NAME in args
    assert args[args.index("/mo") + 1] == "7"
    assert any("--watchdog" in a for a in args)


def test_interval_is_clamped_to_at_least_one():
    args = build_create_args(0, "cmd")
    assert args[args.index("/mo") + 1] == "1"


def test_install_and_remove_use_injected_runner():
    seen: list[list[str]] = []

    def runner(args):
        seen.append(args)
        return 0

    assert watchdog_task.install(5, runner=runner) is True
    assert watchdog_task.remove(runner=runner) is True
    assert seen[0][:2] == ["schtasks", "/create"]
    assert seen[1][:2] == ["schtasks", "/delete"]


def test_apply_creates_when_enabled_deletes_when_disabled():
    verbs: list[str] = []

    def runner(args):
        verbs.append(args[1])
        return 0

    watchdog_task.apply(True, 5, runner=runner)
    watchdog_task.apply(False, 5, runner=runner)
    assert verbs == ["/create", "/delete"]


def test_watchdog_command_contains_flag():
    assert WATCHDOG_FLAG in watchdog_command()
