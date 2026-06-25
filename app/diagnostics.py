"""Диагностика зависаний и утечек ресурсов (расследование крашей).

Зачем: приложение иногда переставало отвечать «без следа» — в app.log не было ни
traceback, ни ошибки (нативный обвал/блокировка COM/Qt себя в Python не показывает),
а Windows писала в журнал только RADAR_PRE_LEAK_64 (рост потребления ресурсов). Этот
модуль превращает «нет следов» в конкретные данные:

  1. **Детектор зависаний** (`install_stall_detector`): UI-поток раз в секунду «пульсирует»
     через QTimer. Отдельный демон-поток (НЕ зависит от цикла событий Qt) проверяет пульс;
     если он устарел дольше порога — значит UI-поток/процесс завис → дамп стеков ВСЕХ
     потоков через faulthandler в logs/stall.log и предупреждение в app.log. Так следующий
     зависон оставит точную точку блокировки.

  2. **Сэмплер ресурсов** (`start_resource_sampler`): демон-поток раз в N секунд пишет в
     лог рабочее множество памяти (RSS), число GDI- и USER-объектов, число хэндлов и число
     потоков Python. Монотонный рост любой из величин = утечка соответствующего класса.
     Поскольку сэмплер не зависит от Qt, он ещё и отличает «завис цикл событий Qt» (пульс
     устарел, но сэмплер продолжает писать) от «завис весь процесс» (молчат оба).

Всё через ctypes — без новой зависимости в собранном .exe. Любая ошибка измерения гасится
(возвращаем -1), чтобы диагностика никогда не уронила приложение.
"""
from __future__ import annotations

import ctypes
import faulthandler
import logging
import os
import sys
import threading
import time
from ctypes import wintypes
from datetime import datetime
from pathlib import Path
from typing import Callable

from . import paths

log = logging.getLogger(__name__)

# Порог простоя UI-потока (сек), после которого считаем приложение зависшим и снимаем
# дампы стеков. Цикл событий Qt не должен молчать дольше пары секунд — 30с заведомо
# аномалия. Переопределяется переменной окружения для отладки.
STALL_TIMEOUT_SECONDS = float(os.environ.get("ZZAP_DIAG_STALL_SECONDS", "30"))
# Период выборки ресурсов (сек). В проде редко (нагрузка на лог ничтожна), в соак-тесте
# можно участить через переменную окружения.
SAMPLE_INTERVAL_SECONDS = float(os.environ.get("ZZAP_DIAG_SAMPLE_SECONDS", "300"))

_IS_WIN = sys.platform == "win32"
_last_pulse = time.monotonic()
_stall_file = None  # держим файл открытым на всё время жизни процесса (для faulthandler)


# ---------------------------------------------------------------------------
# Измерение ресурсов процесса (ctypes; только Windows)
# ---------------------------------------------------------------------------
class _PROCESS_MEMORY_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


# Прототипы WinAPI объявляем явно: иначе ctypes считает HANDLE 32-битным и псевдо-хэндл
# GetCurrentProcess() (-1) обрезается до 0x00000000FFFFFFFF — вызовы тихо падают (это и
# давало RSS=-1/хэндлы=-1 при самопроверке).
_proc_handle = None
if _IS_WIN:
    try:
        _k32 = ctypes.windll.kernel32
        _psapi = ctypes.windll.psapi
        _user32 = ctypes.windll.user32
        _k32.GetCurrentProcess.restype = wintypes.HANDLE
        _psapi.GetProcessMemoryInfo.argtypes = [
            wintypes.HANDLE, ctypes.POINTER(_PROCESS_MEMORY_COUNTERS), wintypes.DWORD]
        _psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        _k32.GetProcessHandleCount.argtypes = [
            wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        _k32.GetProcessHandleCount.restype = wintypes.BOOL
        _user32.GetGuiResources.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        _user32.GetGuiResources.restype = wintypes.DWORD
        _proc_handle = _k32.GetCurrentProcess()
    except Exception:  # noqa: BLE001 - без измерений приложение всё равно работает
        _proc_handle = None


def _rss_mb() -> float:
    if _proc_handle is None:
        return -1.0
    try:
        counters = _PROCESS_MEMORY_COUNTERS()
        counters.cb = ctypes.sizeof(counters)
        if _psapi.GetProcessMemoryInfo(_proc_handle, ctypes.byref(counters), counters.cb):
            return counters.WorkingSetSize / (1024 * 1024)
    except Exception:  # noqa: BLE001 - измерение не должно ничего ломать
        pass
    return -1.0


def _gui_objects(flag: int) -> int:
    # GetGuiResources: flag 0 = GDI-объекты, 1 = USER-объекты текущего процесса.
    if _proc_handle is None:
        return -1
    try:
        return int(_user32.GetGuiResources(_proc_handle, flag))
    except Exception:  # noqa: BLE001
        return -1


def _handle_count() -> int:
    if _proc_handle is None:
        return -1
    try:
        count = wintypes.DWORD(0)
        if _k32.GetProcessHandleCount(_proc_handle, ctypes.byref(count)):
            return int(count.value)
    except Exception:  # noqa: BLE001
        pass
    return -1


def sample() -> dict:
    """Снимок потребления ресурсов процессом (для логов/соак-теста)."""
    return {
        "rss_mb": round(_rss_mb(), 1),
        "gdi": _gui_objects(0),
        "user": _gui_objects(1),
        "handles": _handle_count(),
        "threads": threading.active_count(),
    }


def format_sample(s: dict) -> str:
    return (f"RSS={s['rss_mb']}МБ GDI={s['gdi']} USER={s['user']} "
            f"хэндлы={s['handles']} потоки={s['threads']}")


# ---------------------------------------------------------------------------
# Сэмплер ресурсов (демон-поток)
# ---------------------------------------------------------------------------
def start_resource_sampler(interval_s: float = SAMPLE_INTERVAL_SECONDS) -> threading.Thread:
    """Раз в ``interval_s`` секунд пишет снимок ресурсов в лог. Возвращает поток."""
    def _loop() -> None:
        log.info("Диагностика: сэмплер ресурсов запущен (период %.0fс). %s",
                 interval_s, format_sample(sample()))
        while True:
            time.sleep(interval_s)
            try:
                log.info("Диагностика ресурсов: %s", format_sample(sample()))
            except Exception as e:  # noqa: BLE001
                log.debug("resource sample failed: %s", e)

    t = threading.Thread(target=_loop, name="zzap-resource-sampler", daemon=True)
    t.start()
    return t


# ---------------------------------------------------------------------------
# Детектор зависаний (faulthandler + проверка пульса UI-потока)
# ---------------------------------------------------------------------------
def pulse() -> None:
    """Вызывается с UI-потока (через QTimer): «цикл событий жив прямо сейчас»."""
    global _last_pulse
    _last_pulse = time.monotonic()


def _open_stall_file() -> object | None:
    global _stall_file
    if _stall_file is not None:
        return _stall_file
    try:
        logs = paths.logs_dir()
        logs.mkdir(parents=True, exist_ok=True)
        path = logs / "stall.log"
        # line-buffered, append — файл живёт всё время процесса (нужен faulthandler).
        _stall_file = open(path, "a", buffering=1, encoding="utf-8")
        return _stall_file
    except OSError as e:
        log.warning("Диагностика: не удалось открыть stall.log: %s", e)
        return None


def install_stall_detector(timeout_s: float = STALL_TIMEOUT_SECONDS,
                           *, now: Callable[[], float] = time.monotonic) -> threading.Thread:
    """Снимать дамп стеков всех потоков, если UI-поток не пульсирует дольше ``timeout_s``.

    Также включает faulthandler, чтобы нативный фатальный сбой тоже оставил стек в файле.
    Вернёт демон-поток наблюдателя. ``pulse`` нужно дёргать с UI-потока (QTimer).
    """
    global _last_pulse
    _last_pulse = now()
    stall_file = _open_stall_file()
    if stall_file is not None:
        try:
            faulthandler.enable(file=stall_file)  # нативный обвал тоже сбросит стеки
        except Exception as e:  # noqa: BLE001
            log.debug("faulthandler.enable failed: %s", e)

    def _watch() -> None:
        dumped = False  # один дамп на один эпизод зависания (не спамим)
        while True:
            time.sleep(1.0)
            stale = now() - _last_pulse
            if stale > timeout_s:
                if not dumped:
                    log.warning("Диагностика: UI-поток не отвечает %.0fс — снимаю дамп "
                                "стеков всех потоков (logs/stall.log).", stale)
                    if stall_file is not None:
                        try:
                            stall_file.write(
                                f"\n===== STALL {datetime.now().isoformat(timespec='seconds')} "
                                f"(пульс устарел на {stale:.0f}с) =====\n")
                            faulthandler.dump_traceback(file=stall_file, all_threads=True)
                            stall_file.write(f"ресурсы: {format_sample(sample())}\n")
                        except Exception as e:  # noqa: BLE001
                            log.debug("stall dump failed: %s", e)
                    dumped = True
            else:
                if dumped:
                    log.warning("Диагностика: UI-поток снова отвечает (был зависон).")
                dumped = False

    t = threading.Thread(target=_watch, name="zzap-stall-detector", daemon=True)
    t.start()
    return t
