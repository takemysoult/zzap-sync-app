"""Main window — a tabbed shell hosting the five screens.

Owns the single :class:`AsyncRunner` (all off-thread work) and refreshes the
cells/status tabs when they are shown so newly added cabinets/cells/runs appear
without a manual reload.
"""
from __future__ import annotations

from PySide6.QtWidgets import QMainWindow, QTabWidget, QWidget

from .context import AppContext
from .screens.cabinets import CabinetsScreen
from .screens.cells import CellsScreen
from .screens.connection import ConnectionScreen
from .screens.settings import SettingsScreen
from .screens.status import StatusScreen
from .workers import AsyncRunner


class MainWindow(QMainWindow):
    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.runner = AsyncRunner(self)
        self.setWindowTitle("ZZap Sync — синхронизация прайсов 1С → ZZap")
        self.resize(900, 680)

        self.tabs = QTabWidget()
        self.connection = ConnectionScreen(ctx, self.runner)
        self.cabinets = CabinetsScreen(ctx)
        self.cells = CellsScreen(ctx, self.runner)
        self.settings = SettingsScreen(ctx)
        self.status = StatusScreen(ctx)

        self.tabs.addTab(self.connection, "Подключение 1С")
        self.tabs.addTab(self.cabinets, "Кабинеты ZZap")
        self.tabs.addTab(self.cells, "Ячейки")
        self.tabs.addTab(self.settings, "Настройки")
        self.tabs.addTab(self.status, "Журнал")
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self.setCentralWidget(self.tabs)

    def _on_tab_changed(self, index: int) -> None:
        widget = self.tabs.widget(index)
        # Cabinets feed the cell editor; cells feed the status filter — refresh on show.
        if widget is self.cells:
            self.cells.reload()
        elif widget is self.status:
            self.status.reload()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        self.runner.wait(5000)
        super().closeEvent(event)
