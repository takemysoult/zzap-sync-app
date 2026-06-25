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
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from . import paths

log = logging.getLogger(__name__)

STALE_AFTER = timedelta(minutes=5)


def write_heartbeat() -> None:
    """Called by the running app on a timer — record 'I am alive at <now>'.

    Запись АТОМАРНА (temp-файл + os.replace): иначе сторож мог прочитать heartbeat ровно
    в момент перезаписи (write_text сначала обнуляет файл), увидеть пустоту и ошибочно
    счесть живое приложение упавшим. os.replace заменяет файл целиком одним действием —
    читатель всегда видит либо старое, либо новое содержимое, но не пустоту.
    """
    try:
        p = paths.heartbeat_path()
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(datetime.now().isoformat(timespec="seconds"), encoding="utf-8")
        os.replace(tmp, p)
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


def _default_kill_stale() -> None:
    """Kill any lingering app instance (the frozen exe) except this watchdog process.

    A hung instance keeps holding the single-instance lock, which would make the
    relaunched copy detect it and exit — so the app could never recover. Removing the
    stale process first lets the fresh start become the primary. Only acts on the
    frozen exe (in dev the app is ``python -m app.gui`` and we must not mass-kill python).
    """
    if not getattr(sys, "frozen", False):
        return
    exe_name = Path(sys.executable).name           # ZZapSync.exe
    try:
        subprocess.run(["taskkill", "/F", "/IM", exe_name, "/FI", f"PID ne {os.getpid()}"],
                       capture_output=True, text=True)
    except OSError as e:  # noqa: BLE001
        log.warning("watchdog: не удалось завершить зависший экземпляр: %s", e)


def _default_notify(text: str) -> None:
    try:
        import ctypes
        # MB_OK | MB_ICONINFORMATION | MB_SETFOREGROUND.
        flags = 0x40 | 0x10000
        user32 = ctypes.windll.user32
        # ВАЖНО: НЕ блокирующее модальное окно. Раньше MessageBoxW ждал нажатия «ОК» и
        # держал процесс сторожа живым (зависший процесс + окно копились при перезапусках).
        # MessageBoxTimeoutW сам закрывается через 20с, поэтому сторож не зависает.
        if hasattr(user32, "MessageBoxTimeoutW"):
            user32.MessageBoxTimeoutW(0, text, "ZZap Sync", flags, 0, 20000)
        else:  # запасной путь: показать в демон-потоке, не блокируя выход процесса
            import threading
            threading.Thread(
                target=lambda: user32.MessageBoxW(0, text, "ZZap Sync", flags),
                daemon=True).start()
    except Exception as e:  # noqa: BLE001
        log.warning("watchdog notify failed: %s", e)


def run_watchdog(*, now: Callable[[], datetime] = datetime.now,
                 relaunch: Callable[[], None] = _default_relaunch,
                 notify: Callable[[str], None] = _default_notify,
                 kill_stale: Callable[[], None] = _default_kill_stale,
                 read_heartbeat: Callable[[], datetime | None] = _read_heartbeat,
                 recheck_delay: float = 3.0) -> int:
    """Entry for ``app.gui --watchdog``. Returns 1 if it acted (app was down), else 0."""
    if not _watchdog_enabled():
        return 0
    if not app_is_down(read_heartbeat(), now()):
        return 0  # app is alive (fresh heartbeat)
    # Дебаунс: одного «упал» мало. Heartbeat пишется часто; единичный сбойный/устаревший
    # замер не должен приводить к убийству живого приложения. Ждём и перепроверяем — и
    # действуем, только если приложение ВСЁ ЕЩЁ не отвечает.
    if recheck_delay > 0:
        time.sleep(recheck_delay)
    if not app_is_down(read_heartbeat(), now()):
        log.info("watchdog: heartbeat снова свежий — ложная тревога, ничего не делаю.")
        return 0
    log.warning("watchdog: приложение не отвечает — перезапускаю.")
    try:
        kill_stale()          # remove a hung instance so it can't block the restart
        time.sleep(1.0)       # let the OS release the single-instance lock
        relaunch()
    except Exception as e:  # noqa: BLE001
        log.error("watchdog: не удалось перезапустить: %s", e)
    notify("ZZap Sync не отвечал и был перезапущен автоматически.\n"
           "Если это повторяется — сообщите администратору.")
    return 1
