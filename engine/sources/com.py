"""Чтение прайс-листа из 1С через внешнее COM-соединение (Windows).

Требует установленную платформу 1С той же разрядности, что и Python (x64),
и пакет pywin32. Запрос на языке 1С (ComConfig.query) должен возвращать ровно
5 колонок в порядке: Производитель, Номер, Наименование, Количество, Цена.

КРИТИЧНО (PROJECT_MEMORY §1):
  - COM апартаментно-потоковый: поток, работающий с COM, сам вызывает
    CoInitialize/CoUninitialize. Длинные запросы — на рабочем потоке, не на UI.
  - Перед CoUninitialize обнуляем все COM-объекты и делаем gc.collect(), иначе
    их финализация после деинициализации COM роняет процесс (segfault).
"""
from __future__ import annotations

import logging
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


class ComPriceSource:
    """PriceSource backed by a 1C external (COM) connection.

    One instance carries the connection string + query for one cell. `fetch_rows`
    opens the connection, runs the query, and returns rows — with the segfault-safe
    teardown. Pure Windows/COM; import of pywin32 is deferred to call time so the
    module imports cleanly on any platform (and in CI).
    """

    def __init__(self, cfg: ComConfig) -> None:
        self.cfg = cfg

    def fetch_rows(self) -> list[PriceRow]:
        cfg = self.cfg
        if not cfg.conn_string:
            raise ValueError("Не задана строка соединения (ComConfig.conn_string)")
        if not cfg.query:
            raise ValueError("Не задан текст запроса (ComConfig.query)")

        try:
            import pythoncom
            import win32com.client.dynamic
        except ImportError as e:  # pragma: no cover - зависит от окружения
            raise RuntimeError(
                "Для COM-соединения нужен pywin32 (Windows). Установите: pip install pywin32"
            ) from e

        import gc

        connector = conn = query = result = selection = None
        pythoncom.CoInitialize()
        try:
            log.info("COM: подключаюсь к 1С через %s ...", cfg.progid)
            # dynamic.Dispatch — позднее связывание через IDispatch (надёжно для 1С).
            connector = win32com.client.dynamic.Dispatch(cfg.progid)
            conn = connector.Connect(cfg.conn_string)

            log.info("COM: выполняю запрос...")
            query = conn.NewObject("Query")
            query.Text = cfg.query
            result = query.Execute()
            selection = result.Select()

            rows: list[PriceRow] = []
            while selection.Next():
                rows.append(
                    PriceRow(
                        producer=_to_str(selection.Get(0)),
                        number=_to_str(selection.Get(1)),
                        name=_to_str(selection.Get(2)),
                        quantity=_to_number(selection.Get(3)),
                        price=_to_number(selection.Get(4)),
                    )
                )
            log.info("COM: получено строк: %d", len(rows))
            return rows
        finally:
            # Освобождаем COM-объекты ДО CoUninitialize — иначе их финализация
            # после деинициализации COM роняет процесс (segfault).
            selection = result = query = conn = connector = None
            gc.collect()
            pythoncom.CoUninitialize()


def fetch_rows(cfg: ComConfig) -> list[PriceRow]:
    """Backward-compatible functional entry point (delegates to ComPriceSource)."""
    return ComPriceSource(cfg).fetch_rows()
