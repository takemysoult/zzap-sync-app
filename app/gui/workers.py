"""Run blocking work (1C COM, ZZap HTTP, CellRunner) off the Qt UI thread.

PROJECT_MEMORY §1: COM is apartment-threaded and long 1C/network calls must never
block the UI. Every such call goes through :meth:`AsyncRunner.submit`, which runs
the callable on a ``QThreadPool`` worker and delivers either the result or a
**redacted** error string back on the UI thread.

Threading correctness: the worker's signals are connected to *bound methods of
AsyncRunner* — a QObject that lives on the UI thread. With Qt's AutoConnection the
receiver's thread affinity decides delivery, so a signal emitted from a pool thread
to a UI-thread QObject is delivered **queued, on the UI thread**. (Connecting a
signal to a bare lambda/closure instead would use a *direct* connection and run the
callback on the worker thread — fatal for code that touches widgets.)

A worker callable that touches the database must open its OWN ``Database`` (the DAL
is thread-affine) — see :meth:`AppContext.new_db`.
"""
from __future__ import annotations

import logging
from typing import Callable

from PySide2.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

from ..services.connection import error_text

log = logging.getLogger(__name__)


class _TaskSignals(QObject):
    finished = Signal(object)   # result of the callable
    failed = Signal(str)        # redacted error text (never contains a secret)


class _Task(QRunnable):
    def __init__(self, fn: Callable, args: tuple, kwargs: dict,
                 on_ok: Callable[[object], None],
                 on_err: Callable[[str], None] | None) -> None:
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs
        self.on_ok = on_ok
        self.on_err = on_err
        # Created on the UI thread (submit runs there) -> UI-thread affinity, so the
        # queued delivery below lands on the UI thread.
        self.signals = _TaskSignals()
        # AsyncRunner keeps a Python ref until completion; don't let the pool delete us.
        self.setAutoDelete(False)

    @Slot()
    def run(self) -> None:  # executes on a pool thread
        try:
            result = self._fn(*self._args, **self._kwargs)
        except Exception as e:  # noqa: BLE001 - surface a redacted message, never crash the pool
            msg = error_text(e)
            log.warning("Async task failed: %s", msg)
            self.signals.failed.emit(msg)
        else:
            self.signals.finished.emit(result)


class AsyncRunner(QObject):
    """Owns a QThreadPool and dispatches task results back onto the UI thread."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._pool = QThreadPool(self)
        self._tasks: dict[_TaskSignals, _Task] = {}

    def submit(self, fn: Callable, on_ok: Callable[[object], None],
               on_err: Callable[[str], None] | None = None,
               *args, **kwargs) -> None:
        """Run ``fn(*args, **kwargs)`` on a worker; deliver result/err on the UI thread."""
        task = _Task(fn, args, kwargs, on_ok, on_err)
        self._tasks[task.signals] = task
        # Connect to bound methods of self (UI-thread QObject) => queued delivery.
        task.signals.finished.connect(self._on_finished)
        task.signals.failed.connect(self._on_failed)
        self._pool.start(task)

    @Slot(object)
    def _on_finished(self, result: object) -> None:
        task = self._tasks.pop(self.sender(), None)
        if task is not None and task.on_ok is not None:
            task.on_ok(result)

    @Slot(str)
    def _on_failed(self, msg: str) -> None:
        task = self._tasks.pop(self.sender(), None)
        if task is not None and task.on_err is not None:
            task.on_err(msg)

    def wait(self, msecs: int = -1) -> bool:
        """Block until all queued tasks finish (used on shutdown / in tests)."""
        return self._pool.waitForDone(msecs)
