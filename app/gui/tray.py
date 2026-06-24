"""Системный трей (Phase 4): фоновая работа, меню и уведомления (RU).

Приложение работает свёрнутым в трей и продолжает выгружать по расписанию. Меню даёт
быстрый доступ: время следующего запуска, итог последних загрузок, «Запустить сейчас»
(все/ячейка), «Открыть окно», «Выход». Результаты плановых/ручных выгрузок приходят
всплывающими уведомлениями.

Доставка результатов на UI-поток: `SchedulerService` зовёт `TrayController.listener`
из СВОЕГО рабочего потока; listener лишь эмитит Qt-сигнал моста (`_Bridge`), который
живёт на UI-потоке → AutoConnection доставляет его в слот ОЧЕРЕДЬЮ на UI-поток (тот же
приём, что и в `app/gui/workers.py`). Трогать виджеты прямо из рабочего потока нельзя.
"""
from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from .. import paths  # noqa: F401 - kept for parity with other gui modules
from ..services.scheduler import KIND_FLUSH, RunSummary, SchedulerService
from . import theme
from .context import AppContext

log = logging.getLogger(__name__)


class _Bridge(QObject):
    """UI-thread QObject; cross-thread `emit` is delivered queued onto the UI thread."""
    run_finished = Signal(object)   # RunSummary


class TrayController(QObject):
    def __init__(self, ctx: AppContext, service: SchedulerService, window,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.service = service
        self.window = window
        self._last: RunSummary | None = None

        self._bridge = _Bridge()
        self._bridge.run_finished.connect(self._on_run_finished)

        self.tray = QSystemTrayIcon(self._make_icon(), self)
        self.tray.setToolTip("ZZap Sync — синхронизация прайсов 1С → ZZap")
        self._menu = QMenu()
        self._menu.aboutToShow.connect(self._rebuild_menu)  # keep the cell list fresh
        self.tray.setContextMenu(self._menu)
        self.tray.activated.connect(self._on_activated)
        self._rebuild_menu()
        self.tray.show()

        # Periodically refresh the "next run" line (the scheduler advances it).
        self._timer = QTimer(self)
        self._timer.setInterval(30_000)
        self._timer.timeout.connect(self._refresh_status_lines)
        self._timer.start()

    # --- the listener the scheduler calls (on a worker thread) ------------
    def listener(self, summary: RunSummary) -> None:
        self._bridge.run_finished.emit(summary)

    # --- menu -------------------------------------------------------------
    def _rebuild_menu(self) -> None:
        m = self._menu
        m.clear()
        self._act_next = m.addAction("Следующий запуск: —")
        self._act_next.setEnabled(False)
        self._act_last = m.addAction("Последние загрузки: —")
        self._act_last.setEnabled(False)
        m.addSeparator()

        run_menu = m.addMenu("Запустить сейчас")
        run_menu.addAction("Все включённые ячейки").triggered.connect(self._run_all)
        cells = self.ctx.db.list_cells()
        if cells:
            run_menu.addSeparator()
            for cell in cells:
                act = run_menu.addAction(f"{cell.name} (#{cell.id})")
                act.triggered.connect(
                    lambda checked=False, cid=cell.id: self._run_cell(cid))

        m.addAction("Открыть окно").triggered.connect(self._open_window)
        m.addSeparator()
        m.addAction("Выход").triggered.connect(self._quit)
        self._refresh_status_lines()

    def _refresh_status_lines(self) -> None:
        nrt = self.service.next_run_time
        self._act_next.setText(
            "Следующий запуск: " + (nrt.strftime("%d.%m %H:%M") if nrt else "—"))
        if self._last is not None:
            s = self._last
            self._act_last.setText(
                f"Последние: отправлено {s.posted}, ошибок {s.failed + s.errors}")

    # --- actions ----------------------------------------------------------
    def _run_all(self) -> None:
        self.service.request_run_all()
        self._info("Запускаю все включённые ячейки…")

    def _run_cell(self, cell_id: int) -> None:
        self.service.request_run_cell(cell_id)
        self._info(f"Запускаю ячейку #{cell_id}…")

    def _open_window(self) -> None:
        self.window.showNormal()
        self.window.raise_()
        self.window.activateWindow()

    def _quit(self) -> None:
        # Tell the window to allow a real close, then quit the app loop.
        self.window.request_quit()
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.DoubleClick,
                      QSystemTrayIcon.ActivationReason.Trigger):
            self._open_window()

    # --- run results (UI thread, queued) ---------------------------------
    def _on_run_finished(self, summary: RunSummary) -> None:
        self._last = summary
        self._refresh_status_lines()
        bad = summary.failed + summary.errors
        if summary.kind == KIND_FLUSH:
            msg = f"Досыл отложенного: отправлено {summary.posted}."
        else:
            msg = f"Выгрузка завершена: отправлено {summary.posted}, ошибок {bad}."
        icon = (QSystemTrayIcon.MessageIcon.Warning if bad
                else QSystemTrayIcon.MessageIcon.Information)
        self.tray.showMessage("ZZap Sync", msg, icon, 5000)
        # Refresh the open window so the journal/cells reflect the run.
        if self.window.isVisible():
            self.window.cells.reload()
            self.window.status.reload()

    def _info(self, message: str) -> None:
        self.tray.showMessage("ZZap Sync", message,
                              QSystemTrayIcon.MessageIcon.Information, 3000)

    # --- icon (painted at runtime; a real asset arrives with Phase 6) -----
    @staticmethod
    def _make_icon() -> QIcon:
        pm = QPixmap(64, 64)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.PRIMARY))
        p.drawRoundedRect(4, 4, 56, 56, 14, 14)
        p.setPen(QColor("#FFFFFF"))
        p.setFont(QFont(theme.UI_FAMILY, 30, QFont.Weight.Bold))
        p.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, "Z")
        p.end()
        return QIcon(pm)
