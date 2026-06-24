"""Задача Планировщика Windows для сторожа (Phase 6).

Сторож — ВНЕШНЯЯ задача: её запускает Планировщик Windows раз в `interval_hours`
(только когда ПК включён и пользователь в сеансе), поэтому она переживает падение
самого приложения и может его перезапустить. Задача per-user и интерактивная (`/it`),
чтобы перезапуск и уведомление были видны в сеансе пользователя; права администратора
не нужны.

Вызов `schtasks` вынесен за инжектируемый `runner`, чтобы тесты не трогали реальный
Планировщик.
"""
from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

SETTING_WATCHDOG_ENABLED = "watchdog_enabled"
TASK_NAME = "ZZapSync Watchdog"
WATCHDOG_FLAG = "--watchdog"

# runner(args) -> exit code (0 = success)
Runner = Callable[[list[str]], int]


def _default_runner(args: list[str]) -> int:
    try:
        return subprocess.run(args, capture_output=True, text=True).returncode
    except OSError as e:  # schtasks missing / blocked — don't crash the app
        log.warning("schtasks недоступен: %s", e)
        return 1


def watchdog_command() -> str:
    """Command schtasks runs each interval to perform the watchdog check."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" {WATCHDOG_FLAG}'
    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")
    py = str(pyw if pyw.exists() else exe)
    return f'"{py}" -m app.gui {WATCHDOG_FLAG}'


def build_create_args(interval_hours: int, command: str) -> list[str]:
    interval = max(1, int(interval_hours))
    return ["schtasks", "/create", "/tn", TASK_NAME, "/tr", command,
            "/sc", "HOURLY", "/mo", str(interval), "/it", "/f"]


def install(interval_hours: int, command: str | None = None, *,
            runner: Runner = _default_runner) -> bool:
    code = runner(build_create_args(interval_hours, command or watchdog_command()))
    if code != 0:
        log.warning("Не удалось создать задачу сторожа (schtasks код %s).", code)
    return code == 0


def remove(*, runner: Runner = _default_runner) -> bool:
    return runner(["schtasks", "/delete", "/tn", TASK_NAME, "/f"]) == 0


def apply(enabled: bool, interval_hours: int, *,
          runner: Runner = _default_runner) -> None:
    """Sync the scheduled task to the desired on/off state + interval in one call."""
    if enabled:
        install(interval_hours, runner=runner)
    else:
        remove(runner=runner)
