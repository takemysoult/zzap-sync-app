"""Single-instance guard (Phase 6): не запускать второй экземпляр приложения.

Первенство определяет именованный мьютекс Windows (`CreateMutex`): пока процесс
жив, имя занято; при падении ОС сама закрывает хэндл, поэтому «залипшей»
блокировки не остаётся. (PySide6-версия держала сегмент `QSharedMemory`, но в
PySide2 этот класс не привязан.) Второй запуск обнаруживает занятый мьютекс,
через `QLocalSocket` просит уже работающий экземпляр показать окно и завершается.
"""
from __future__ import annotations

import logging

import win32api
import win32event
import winerror
from PySide2.QtCore import QObject, Signal
from PySide2.QtNetwork import QLocalServer, QLocalSocket

log = logging.getLogger(__name__)

_KEY = "ZZapSyncSingleton"


class SingleInstance(QObject):
    """Primary instance owns the named mutex + a local server; a secondary
    instance pings the primary (to raise its window) and should exit."""

    activated = Signal()   # emitted in the primary when a second launch pings it

    def __init__(self, key: str = _KEY, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._key = key
        # Держим хэндл всю жизнь процесса; ERROR_ALREADY_EXISTS = имя уже занято
        # работающим экземпляром (проверять сразу после CreateMutex).
        self._mutex = win32event.CreateMutex(None, False, key)
        self._server: QLocalServer | None = None
        self._is_primary = win32api.GetLastError() != winerror.ERROR_ALREADY_EXISTS
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
