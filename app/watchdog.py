"""Сторож приложения (Phase 6): запускается Планировщиком Windows раз в интервал.

Логика: приложение, пока живо, обновляет heartbeat-файл (раз в минуту). Сторож
читает его: если отметка свежая — приложение работает, выходим тихо; если устарела
или файла нет — приложение упало/закрыто → перезапускаем его свёрнутым в трей и
показываем пользователю уведомление. Если функция сторожа выключена в Настройках —
ничего не делаем.

Это единственный надёжный способ заметить НАТИВНЫЙ обвал (COM/Qt), который не оставляет
Python-исключения, — само приложение в таком случае себя проверить не может.
"""
from __future__ import annotations

import logging
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from . import paths

log = logging.getLogger(__name__)

STALE_AFTER = timedelta(minutes=5)


def write_heartbeat() -> None:
    """Called by the running app on a timer — record 'I am alive at <now>'."""
    try:
        paths.heartbeat_path().write_text(
            datetime.now().isoformat(timespec="seconds"), encoding="utf-8")
    except OSError as e:  # never let a heartbeat write break the app
        log.debug("heartbeat write failed: %s", e)


def _read_heartbeat() -> datetime | None:
    try:
        return datetime.fromisoformat(
            paths.heartbeat_path().read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def app_is_down(heartbeat: datetime | None, now: datetime,
                stale: timedelta = STALE_AFTER) -> bool:
    """Pure decision: no heartbeat, or it's older than `stale`, ⇒ the app isn't running."""
    return heartbeat is None or (now - heartbeat) > stale


def _watchdog_enabled() -> bool:
    from .db.dal import Database
    from .services.watchdog_task import SETTING_WATCHDOG_ENABLED
    try:
        db = Database(paths.db_path())
        try:
            return db.get_bool(SETTING_WATCHDOG_ENABLED, default=True)
        finally:
            db.close()
    except Exception as e:  # noqa: BLE001 - if we can't read, assume enabled
        log.warning("watchdog: не удалось прочитать настройку: %s", e)
        return True


def _relaunch_command() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "--minimized"]
    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")
    return [str(pyw if pyw.exists() else exe), "-m", "app.gui", "--minimized"]


def _default_relaunch() -> None:
    subprocess.Popen(_relaunch_command(), close_fds=True)


def _default_notify(text: str) -> None:
    try:
        import ctypes
        # MB_OK | MB_ICONINFORMATION | MB_SETFOREGROUND
        ctypes.windll.user32.MessageBoxW(0, text, "ZZap Sync", 0x40 | 0x10000)
    except Exception as e:  # noqa: BLE001
        log.warning("watchdog notify failed: %s", e)


def run_watchdog(*, now: Callable[[], datetime] = datetime.now,
                 relaunch: Callable[[], None] = _default_relaunch,
                 notify: Callable[[str], None] = _default_notify) -> int:
    """Entry for ``app.gui --watchdog``. Returns 1 if it acted (app was down), else 0."""
    if not _watchdog_enabled():
        return 0
    if not app_is_down(_read_heartbeat(), now()):
        return 0  # app is alive
    log.warning("watchdog: приложение не отвечает — перезапускаю.")
    try:
        relaunch()
    except Exception as e:  # noqa: BLE001
        log.error("watchdog: не удалось перезапустить: %s", e)
    notify("ZZap Sync не отвечал и был перезапущен автоматически.\n"
           "Если это повторяется — сообщите администратору.")
    return 1
