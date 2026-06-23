"""Диагностика COM-подключения к 1С.

REFERENCE/BLUEPRINT ONLY — это исходник из старого CLI (читал config.ini).
В приложении его логику заменяет ConnectionManager (Phase 1), который берёт
параметры из SQLite/DPAPI, а не из config.ini. Здесь сохранён как образец
правильного COM-подключения и разбора ошибок 1С.

Запуск 1С x64 → 64-битный Python (см. PROJECT_MEMORY §1).
"""
from __future__ import annotations

import configparser
import struct
import sys
from pathlib import Path

from engine.config import ComConfig

BASE_DIR = Path(__file__).resolve().parent
PREVIEW = 10


def _load_com_section() -> ComConfig:
    """Читаем только [com] напрямую — без проверки api_key (это тест связи с 1С)."""
    cp = configparser.ConfigParser(interpolation=None)
    cp.optionxform = str
    cp.read(BASE_DIR / "config.ini", encoding="utf-8")
    g = lambda k, d="": cp.get("com", k, fallback=d).strip()  # noqa: E731
    return ComConfig(progid=g("progid", "V83.COMConnector"),
                     conn_string=g("conn_string"), query=g("query"))


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    print(f"Python: {sys.version.split()[0]}, {struct.calcsize('P') * 8}-bit")

    com = _load_com_section()
    print(f"ProgID: {com.progid}")
    print(f"Строка соединения: {com.conn_string}")

    try:
        import pythoncom
        import win32com.client.dynamic
    except ImportError:
        print("ОШИБКА: не установлен pywin32. В 32-битном venv: pip install pywin32")
        return 1

    import gc

    connector = conn = query = result = sel = None
    pythoncom.CoInitialize()
    try:
        print("Подключаюсь...")
        # dynamic.Dispatch — позднее связывание через IDispatch (надёжно для 1С).
        connector = win32com.client.dynamic.Dispatch(com.progid)
        conn = connector.Connect(com.conn_string)
        print("Соединение установлено. Выполняю запрос...")

        query = conn.NewObject("Query")
        query.Text = com.query
        result = query.Execute()
        sel = result.Select()

        cols = ["Производитель", "Номер", "Наименование", "Количество", "Цена"]
        print(" | ".join(cols))
        print("-" * 80)
        n = 0
        while sel.Next():
            n += 1
            if n <= PREVIEW:
                row = [sel.Get(0), sel.Get(1), sel.Get(2), sel.Get(3), sel.Get(4)]
                print(" | ".join(str(x) for x in row))
        print("-" * 80)
        print(f"Всего строк в выборке: {n}")
        if n == 0:
            print("Строк нет. Проверьте: вид цены, остатки > 0, имена справочников/регистров.")
        return 0
    except pythoncom.com_error as e:  # noqa: PERF203
        print("ОШИБКА COM при подключении/запросе.")
        try:
            hresult, msg, exc, _arg = e.args
            print(f"  HRESULT: {hresult} ({hresult & 0xFFFFFFFF:#010x})")
            print(f"  Сообщение: {msg}")
            if exc:
                # exc = (wcode, source, description, helpfile, helpctx, scode)
                print(f"  Источник 1С: {exc[1]}")
                print(f"  Описание 1С: {exc[2]}")
        except Exception:  # noqa: BLE001
            print(f"  {e}")
        print("Подсказки: версия платформы vs сервер; логин/пароль 1С; права пользователя.")
        return 1
    except Exception as e:  # noqa: BLE001
        print(f"ОШИБКА: {e!r}")
        return 1
    finally:
        # Освобождаем COM-объекты ДО CoUninitialize — иначе segfault.
        sel = result = query = conn = connector = None
        gc.collect()
        pythoncom.CoUninitialize()


if __name__ == "__main__":
    sys.exit(main())
