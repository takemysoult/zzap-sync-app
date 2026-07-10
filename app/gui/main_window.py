"""Main window — a tabbed shell hosting the five screens.

Owns the single :class:`AsyncRunner` (all off-thread work), the Phase 4
:class:`SchedulerService`, and (once :meth:`start_background` runs) the system-tray
controller. Refreshes the cells/status tabs when shown so newly added
cabinets/cells/runs appear without a manual reload.

Background model (Phase 4): the app runs minimized to the tray and keeps uploading on
schedule. :meth:`start_background` (called only from ``app.main``) starts the scheduler
and, when a system tray is available, switches the window to close-to-tray. Smoke tests
construct ``MainWindow`` without starting the background, so closing behaves exactly as
before for them.
"""
from __future__ import annotations

import logging
import threading

from PySide2.QtCore import QTimer
from PySide2.QtWidgets import QMainWindow, QSystemTrayIcon, QTabWidget, QWidget

from ..services.autostart import AutostartManager
from ..services.scheduler import SchedulerService
from ..services.watchdog_task import SETTING_WATCHDOG_ENABLED
from ..services import watchdog_task
from ..watchdog import write_heartbeat
from .context import AppContext
from .screens.cabinets import CabinetsScreen
from .screens.cells import CellsScreen
from .screens.connection import ConnectionScreen
from .screens.settings import SettingsScreen
from .screens.status import StatusScreen
from .workers import AsyncRunner

log = logging.getLogger(__name__)

SETTING_TRAY_HINT_SHOWN = "tray_hint_shown"


class MainWindow(QMainWindow):
    def __init__(self, ctx: AppContext,
                 scheduler_service: SchedulerService | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.runner = AsyncRunner(self)
        self.service = scheduler_service
        self.autostart = AutostartManager()
        self.tray = None                 # created by start_background (if tray available)
        self._background_active = False   # close-to-tray only after start_background
        self._allow_close = False         # set by "Выход" for a real quit
        self.setWindowTitle("ZZap Sync — синхронизация прайсов 1С → ZZap")
        self.resize(900, 680)

        self.tabs = QTabWidget()
        self.connection = ConnectionScreen(ctx, self.runner)
        self.cabinets = CabinetsScreen(ctx)
        self.cells = CellsScreen(ctx, self.runner, scheduler_service)
        self.settings = SettingsScreen(ctx, scheduler_service, self.autostart)
        self.status = StatusScreen(ctx)

        self.tabs.addTab(self.connection, "Подключение 1С")
        self.tabs.addTab(self.cabinets, "Кабинеты ZZap")
        self.tabs.addTab(self.cells, "Ячейки")
        self.tabs.addTab(self.settings, "Настройки")
        self.tabs.addTab(self.status, "Журнал")
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self.setCentralWidget(self.tabs)

    # --- background (scheduler + tray) -----------------------------------
    def start_background(self, minimized: bool = False) -> None:
        """Start the scheduler and, if a tray is available, run minimized to it.

        Called only from ``app.main`` — never from the smoke tests.
        """
        if self.service is None:
            self.show()
            return
        tray_ok = QSystemTrayIcon.isSystemTrayAvailable()
        if tray_ok:
            from .tray import TrayController
            self.tray = TrayController(self.ctx, self.service, self)
            self.service.set_listener(self.tray.listener)
            self._background_active = True
        else:
            # No tray: don't close-to-tray (the user could never get the window back),
            # and make closing the window quit the app (app.main set quit-on-close off
            # for the tray case).
            log.warning("Системный трей недоступен — окно останется видимым.")
            from PySide2.QtWidgets import QApplication
            app = QApplication.instance()
            if app is not None:
                app.setQuitOnLastWindowClosed(True)
        self.service.start()
        self._start_heartbeat()
        # Defer the watchdog-task setup until AFTER the event loop is running, and run
        # the (blocking) schtasks call off the UI thread — otherwise a slow schtasks
        # could freeze startup before the first heartbeat/exec and look like a hang.
        QTimer.singleShot(1500, self._ensure_watchdog_task)
        if minimized and tray_ok:
            self.hide()
        else:
            self.show()

    def _start_heartbeat(self) -> None:
        """Touch the heartbeat file now and every 60 s so the external watchdog can
        tell the app is alive."""
        write_heartbeat()
        self._heartbeat_timer = QTimer(self)
        self._heartbeat_timer.setInterval(60_000)
        self._heartbeat_timer.timeout.connect(write_heartbeat)
        self._heartbeat_timer.start()

    def _ensure_watchdog_task(self) -> None:
        """Create/refresh (or remove) the Windows watchdog task per the saved setting.

        The schtasks call runs in a background thread so it never blocks the UI thread.
        """
        enabled = self.ctx.db.get_bool(SETTING_WATCHDOG_ENABLED, default=True)

        def _apply() -> None:
            try:
                watchdog_task.apply(enabled)
            except Exception as e:  # noqa: BLE001 - task setup must never block startup
                log.warning("Не удалось настроить задачу сторожа: %s", e)

        threading.Thread(target=_apply, daemon=True).start()

    def bring_to_front(self) -> None:
        """Show + raise the window (used when a second launch pings us)."""
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def request_quit(self) -> None:
        """Allow the next close to actually close (used by the tray «Выход»)."""
        self._allow_close = True

    def _on_tab_changed(self, index: int) -> None:
        widget = self.tabs.widget(index)
        # Cabinets feed the cell editor; cells feed the status filter — refresh on show.
        if widget is self.cells:
            self.cells.reload()
        elif widget is self.status:
            self.status.reload()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        # Close-to-tray: hide instead of quit while the background is running.
        if self._background_active and not self._allow_close:
            event.ignore()
            self.hide()
            self._maybe_show_tray_hint()
            return
        self.runner.wait(5000)
        super().closeEvent(event)

    def _maybe_show_tray_hint(self) -> None:
        if self.tray is None or self.ctx.db.get_bool(SETTING_TRAY_HINT_SHOWN):
            return
        self.tray.tray.showMessage(
            "ZZap Sync продолжает работать",
            "Приложение свёрнуто в трей и выгружает по расписанию. "
            "Для выхода: правый клик по значку → «Выход».",
            QSystemTrayIcon.Information, 6000)
        self.ctx.db.set_bool(SETTING_TRAY_HINT_SHOWN, True)
