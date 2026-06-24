"""Single-instance guard (Phase 6): не запускать второй экземпляр приложения.

На Windows один работающий процесс держит сегмент `QSharedMemory`; при падении
процесса ОС сама его освобождает, поэтому «залипшей» блокировки не остаётся (в
отличие от Linux). Второй запуск обнаруживает занятый сегмент, через `QLocalSocket`
просит уже работающий экземпляр показать окно и завершается.
"""
from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

try:  # QSharedMemory lives in QtCore on PySide6
    from PySide6.QtCore import QSharedMemory
except ImportError:  # pragma: no cover
    from PySide6.QtGui import QSharedMemory  # type: ignore

log = logging.getLogger(__name__)

_KEY = "ZZapSyncSingleton"


class SingleInstance(QObject):
    """Primary instance owns the shared-memory segment + a local server; a secondary
    instance pings the primary (to raise its window) and should exit."""

    activated = Signal()   # emitted in the primary when a second launch pings it

    def __init__(self, key: str = _KEY, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._key = key
        self._mem = QSharedMemory(key)
        self._server: QLocalServer | None = None
        self._is_primary = self._mem.create(1)
        if self._is_primary:
            QLocalServer.removeServer(key)  # clear a stale socket from a hard crash
            self._server = QLocalServer(self)
            self._server.newConnection.connect(self._on_new_connection)
            self._server.listen(key)

    def is_primary(self) -> bool:
        return self._is_primary

    def ping_primary(self) -> None:
        """Ask the already-running instance to surface its window."""
        sock = QLocalSocket()
        sock.connectToServer(self._key)
        if sock.waitForConnected(500):
            sock.write(b"show")
            sock.waitForBytesWritten(500)
            sock.disconnectFromServer()

    def _on_new_connection(self) -> None:
        if self._server is None:
            return
        conn = self._server.nextPendingConnection()
        if conn is not None:
            self.activated.emit()
            conn.disconnectFromServer()
