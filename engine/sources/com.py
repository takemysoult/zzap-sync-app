"""Чтение данных из 1С через внешнее COM-соединение (Windows).

Требует установленную платформу 1С той же разрядности, что и Python (x64),
и пакет pywin32.

КРИТИЧНО (PROJECT_MEMORY §1):
  - COM апартаментно-потоковый: поток, работающий с COM, сам вызывает
    CoInitialize/CoUninitialize. Длинные запросы — на рабочем потоке, не на UI.
  - Перед CoUninitialize обнуляем все COM-объекты и делаем gc.collect(), иначе
    их финализация после деинициализации COM роняет процесс (segfault).

`Com1C` — контекст-менеджер одного соединения: открывает (CoInitialize + Connect),
позволяет выполнить произвольное число запросов, и в __exit__ делает безопасный
teardown. На нём построены и выгрузка прайса (ComPriceSource), и разведка
справочников (ConnectionManager).
"""
from __future__ import annotations

import logging
import sys
from typing import Any

from ..config import ComConfig
from ..models import PriceRow

log = logging.getLogger(__name__)


def _to_number(value: Any) -> float:
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _to_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


class Com1C:
    """A single 1C external (COM) connection as a context manager.

    Usage::

        with Com1C(conn_string, progid) as c:
            rows = c.query(text, columns=5)

    `query` returns a list of tuples (one per result row), each with `columns`
    primitive Python values via `Selection.Get(i)`. The teardown nulls COM objects
    and gc-collects before CoUninitialize (segfault-safe).
    """

    def __init__(self, conn_string: str, progid: str = "V83.COMConnector") -> None:
        self.conn_string = conn_string
        self.progid = progid
        self._connector = None
        self._conn = None
        self._pythoncom = None
        self._initialized = False

    def __enter__(self) -> "Com1C":
        try:
            import pythoncom
            import win32com.client.dynamic
        except ImportError as e:  # pragma: no cover - зависит от окружения
            raise RuntimeError(
                "Для COM-соединения нужен pywin32 (Windows). Установите: pip install pywin32"
            ) from e

        self._pythoncom = pythoncom
        pythoncom.CoInitialize()
        self._initialized = True
        try:
            log.info("COM: подключаюсь к 1С через %s ...", self.progid)
            # dynamic.Dispatch — позднее связывание через IDispatch (надёжно для 1С).
            self._connector = win32com.client.dynamic.Dispatch(self.progid)
            self._conn = self._connector.Connect(self.conn_string)
        except BaseException:
            # При сбое подключения всё равно делаем безопасный teardown.
            self.__exit__(*sys.exc_info())
            raise
        return self

    def query(self, text: str, columns: int) -> list[tuple]:
        if self._conn is None:
            raise RuntimeError("Нет активного соединения 1С.")
        q = sel = None
        try:
            q = self._conn.NewObject("Query")
            q.Text = text
            sel = q.Execute().Select()
            rows: list[tuple] = []
            while sel.Next():
                rows.append(tuple(sel.Get(i) for i in range(columns)))
            log.info("COM: запрос вернул строк: %d", len(rows))
            return rows
        finally:
            # Освобождаем COM-объекты запроса до выхода/следующего запроса.
            sel = q = None

    def __exit__(self, *exc) -> bool:
        import gc

        # Освобождаем COM-объекты ДО CoUninitialize — иначе их финализация
        # после деинициализации COM роняет процесс (segfault).
        self._conn = None
        self._connector = None
        gc.collect()
        if self._initialized and self._pythoncom is not None:
            self._pythoncom.CoUninitialize()
            self._initialized = False
        return False


class ComPriceSource:
    """PriceSource backed by a 1C external (COM) connection.

    One instance carries the connection string + query for one cell. Import of
    pywin32 is deferred (inside Com1C) so the module imports cleanly on any platform.
    """

    def __init__(self, cfg: ComConfig) -> None:
        self.cfg = cfg

    def fetch_rows(self) -> list[PriceRow]:
        cfg = self.cfg
        if not cfg.conn_string:
            raise ValueError("Не задана строка соединения (ComConfig.conn_string)")
        if not cfg.query:
            raise ValueError("Не задан текст запроса (ComConfig.query)")

        log.info("COM: выполняю запрос прайса...")
        with Com1C(cfg.conn_string, cfg.progid) as c:
            raw = c.query(cfg.query, 5)
        rows = [
            PriceRow(
                producer=_to_str(r[0]),
                number=_to_str(r[1]),
                name=_to_str(r[2]),
                quantity=_to_number(r[3]),
                price=_to_number(r[4]),
            )
            for r in raw
        ]
        log.info("COM: получено строк прайса: %d", len(rows))
        return rows


def fetch_rows(cfg: ComConfig) -> list[PriceRow]:
    """Backward-compatible functional entry point (delegates to ComPriceSource)."""
    return ComPriceSource(cfg).fetch_rows()
