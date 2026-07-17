"""Автозапуск приложения вместе с Windows (per-user, Phase 4).

Используется per-user ключ реестра
``HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run`` — не требует прав
администратора и не трогает других пользователей. Включение/выключение делается из
вкладки «Настройки»; при автозапуске приложение стартует свёрнутым в трей
(``--minimized``).

Работа с реестром изолирована за маленьким бэкендом (`get`/`set`/`delete`), поэтому
логика менеджера тестируется на словарном фейке — без обращения к настоящему реестру.

ЗАМЕЧАНИЕ (Phase 6): окончательная команда запуска формируется для собранного .exe
(PyInstaller). В режиме разработки команда (`pythonw -m app.gui --minimized`) зависит
от рабочего каталога/окружения и приведена как best-effort.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Protocol

from .. import flavor

log = logging.getLogger(__name__)

SETTING_AUTOSTART = "autostart"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
MINIMIZED_FLAG = "--minimized"


def app_value_name() -> str:
    """Имя значения в Run-ключе — своё у каждого флейвора (ZZapSync / PriceMailer),
    чтобы оба приложения могли автозапускаться независимо."""
    return flavor.app_id()


class RegistryBackend(Protocol):
    """Минимальный интерфейс работы с одним строковым значением в ключе реестра."""
    def get(self, name: str) -> str | None: ...
    def set(self, name: str, value: str) -> None: ...
    def delete(self, name: str) -> None: ...


class WinRegBackend:
    """Реальный бэкенд поверх ``winreg`` (HKCU\\...\\Run). Только Windows."""

    def __init__(self, key_path: str = RUN_KEY) -> None:
        self._key_path = key_path

    def get(self, name: str) -> str | None:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self._key_path, 0,
                                winreg.KEY_READ) as key:
                value, _type = winreg.QueryValueEx(key, name)
                return value
        except FileNotFoundError:
            return None

    def set(self, name: str, value: str) -> None:
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, self._key_path) as key:
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)

    def delete(self, name: str) -> None:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self._key_path, 0,
                                winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, name)
        except FileNotFoundError:
            pass


class AutostartManager:
    def __init__(self, backend: RegistryBackend | None = None,
                 value_name: str | None = None) -> None:
        self._backend = backend or WinRegBackend()
        self._value_name = value_name or app_value_name()

    def is_enabled(self) -> bool:
        return self._backend.get(self._value_name) is not None

    def enable(self, command: str | None = None) -> None:
        """Add/refresh the Run entry (idempotent)."""
        self._backend.set(self._value_name, command or launch_command())

    def disable(self) -> None:
        """Remove the Run entry (idempotent — no error if absent)."""
        self._backend.delete(self._value_name)

    def apply(self, enabled: bool, command: str | None = None) -> None:
        """Sync the registry to the desired on/off state in one call."""
        if enabled:
            self.enable(command)
        else:
            self.disable()


def launch_command(minimized: bool = True) -> str:
    """The command Windows runs at logon. Quoted for paths with spaces.

    - Frozen (PyInstaller, Phase 6): the app exe itself + ``--minimized``.
    - Dev: ``pythonw.exe -m app.gui --minimized`` (best-effort; see module note).
    """
    flag = f" {MINIMIZED_FLAG}" if minimized else ""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"{flag}'
    pyw = _pythonw_path()
    return f'"{pyw}" -m app.gui{flag}'


def _pythonw_path() -> str:
    """pythonw.exe next to the current interpreter (no console window), else python."""
    exe = Path(sys.executable)
    candidate = exe.with_name("pythonw.exe")
    return str(candidate if candidate.exists() else exe)
