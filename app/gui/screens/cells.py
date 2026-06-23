"""Cells screen — the table of upload cells, the duplicate-risk banner, and Run now.

Add/edit/delete cells (via :class:`CellEditor`), see the duplicate-across-warehouses
warning (:func:`find_duplicate_risks`), and run a cell — or all enabled cells — now.
A run that would actually POST to ZZap (global staging OFF *and* the cell's staging
OFF) is confirmed first; the actual staging gate is enforced by CellRunner.

"Run now" opens its OWN ``Database`` inside the worker thread (the DAL is
thread-affine) and runs CellRunner there, off the UI thread.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QHBoxLayout, QHeaderView,
                               QLabel, QMessageBox, QPushButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from ...db.models import Cell
from ...services.cell_runner import (RUN_OK, RUN_RESEND_OK, SETTING_GLOBAL_STAGING,
                                     CellRunner, RunResult)
from ...services.duplicates import SHARED_WAREHOUSE, find_duplicate_risks
from ..context import AppContext
from ..workers import AsyncRunner
from .cell_editor import CellEditor

_USER_ROLE = int(Qt.ItemDataRole.UserRole)


class CellsScreen(QWidget):
    def __init__(self, ctx: AppContext, runner: AsyncRunner,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.runner = runner
        self._build()
        self.reload()

    def _build(self) -> None:
        root = QVBoxLayout(self)

        self.banner = QLabel("")
        self.banner.setWordWrap(True)
        self.banner.setVisible(False)
        root.addWidget(self.banner)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["Название", "Вкл.", "Кабинет", "code_templ", "Вид цены",
             "Склады", "Режим"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(
            5, QHeaderView.ResizeMode.Stretch)
        self.table.doubleClicked.connect(lambda *_: self._edit())
        root.addWidget(self.table, 1)

        btns = QHBoxLayout()
        b_add = QPushButton("Добавить…")
        b_add.clicked.connect(self._add)
        b_edit = QPushButton("Изменить…")
        b_edit.clicked.connect(self._edit)
        b_del = QPushButton("Удалить")
        b_del.clicked.connect(self._delete)
        self.btn_run = QPushButton("Запустить выбранную")
        self.btn_run.clicked.connect(self._run_selected)
        self.btn_run_all = QPushButton("Запустить все включённые")
        self.btn_run_all.clicked.connect(self._run_all)
        for b in (b_add, b_edit, b_del):
            btns.addWidget(b)
        btns.addStretch(1)
        btns.addWidget(self.btn_run)
        btns.addWidget(self.btn_run_all)
        root.addLayout(btns)

        self.lbl_status = QLabel("")
        self.lbl_status.setWordWrap(True)
        root.addWidget(self.lbl_status)

    # --- load ------------------------------------------------------------
    def reload(self) -> None:
        cabinets = {c.id: c.name for c in self.ctx.db.list_cabinets()}
        cells = self.ctx.db.list_cells()
        self.table.setRowCount(len(cells))
        for row, cell in enumerate(cells):
            mode = "staging" if cell.staging_mode else "БОЕВОЙ"
            cab = cabinets.get(cell.cabinet_id, "—")
            values = [
                cell.name,
                "да" if cell.enabled else "нет",
                cab,
                str(cell.code_templ),
                cell.price_type,
                ", ".join(cell.warehouses),
                mode,
            ]
            for col, text in enumerate(values):
                item = QTableWidgetItem(text)
                if col == 0:
                    item.setData(_USER_ROLE, cell.id)
                self.table.setItem(row, col, item)
        self._refresh_banner(cells)

    def _refresh_banner(self, cells: list[Cell]) -> None:
        risks = find_duplicate_risks(cells)
        if not risks:
            self.banner.setVisible(False)
            return
        high = any(r.confidence == SHARED_WAREHOUSE for r in risks)
        color = "#c0392b" if high else "#b9770e"
        head = ("⚠ Возможное задвоение товара в кабинете ZZap "
                f"({len(risks)} предупреждений). ")
        self.banner.setText(head + "Наведите для подробностей.")
        self.banner.setToolTip("\n\n".join(r.message for r in risks))
        self.banner.setStyleSheet(
            f"color: white; background: {color}; padding: 8px; border-radius: 4px;")
        self.banner.setVisible(True)

    def _selected_id(self) -> int | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        return item.data(_USER_ROLE) if item else None

    # --- CRUD ------------------------------------------------------------
    def _add(self) -> None:
        dlg = CellEditor(self.ctx, self.runner, None, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.ctx.db.add_cell(dlg.result_cell())
            self.reload()

    def _edit(self) -> None:
        cell_id = self._selected_id()
        if cell_id is None:
            return
        cell = self.ctx.db.get_cell(cell_id)
        if cell is None:
            return
        dlg = CellEditor(self.ctx, self.runner, cell, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.ctx.db.update_cell(dlg.result_cell())
            self.reload()

    def _delete(self) -> None:
        cell_id = self._selected_id()
        if cell_id is None:
            return
        if QMessageBox.question(self, "Удалить ячейку?",
                                "Удалить выбранную ячейку?") \
                == QMessageBox.StandardButton.Yes:
            self.ctx.db.delete_cell(cell_id)
            self.reload()

    # --- run -------------------------------------------------------------
    def _global_staging(self) -> bool:
        return self.ctx.db.get_bool(SETTING_GLOBAL_STAGING, default=False)

    def _is_real_send(self, cell: Cell) -> bool:
        return not self._global_staging() and not cell.staging_mode

    def _run_selected(self) -> None:
        cell_id = self._selected_id()
        if cell_id is None:
            return
        cell = self.ctx.db.get_cell(cell_id)
        if cell is None:
            return
        if self._is_real_send(cell) and not self._confirm_real(
                f"Ячейка «{cell.name}» будет отправлена в ZZap (шаблон "
                f"{cell.code_templ} будет заменён)."):
            return
        self._set_running(True, f"Выполняю ячейку «{cell.name}»…")
        self.runner.submit(lambda: _run_one(self.ctx, cell_id),
                           self._on_run_done, self._on_run_err)

    def _run_all(self) -> None:
        cells = self.ctx.db.list_cells(enabled_only=True)
        if not cells:
            QMessageBox.information(self, "Нет ячеек",
                                   "Нет включённых ячеек для запуска.")
            return
        real = [c for c in cells if self._is_real_send(c)]
        if real and not self._confirm_real(
                f"Боевая отправка в ZZap затронет ячеек: {len(real)} "
                f"(из {len(cells)} включённых)."):
            return
        self._set_running(True, f"Выполняю включённые ячейки ({len(cells)})…")
        self.runner.submit(lambda: _run_all(self.ctx),
                           self._on_run_all_done, self._on_run_err)

    def _confirm_real(self, detail: str) -> bool:
        return QMessageBox.warning(
            self, "Боевая отправка",
            detail + "\n\nЭто НЕ режим staging. Продолжить отправку?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes

    def _set_running(self, running: bool, message: str = "") -> None:
        self.btn_run.setEnabled(not running)
        self.btn_run_all.setEnabled(not running)
        self.lbl_status.setStyleSheet("color: gray;")
        self.lbl_status.setText(message)

    def _on_run_done(self, result: RunResult) -> None:
        self._set_running(False)
        self._show_result(result)
        self.reload()

    def _on_run_all_done(self, results: list[RunResult]) -> None:
        self._set_running(False)
        ok = sum(1 for r in results if r.status in (RUN_OK, RUN_RESEND_OK))
        self.lbl_status.setStyleSheet("color: black;")
        self.lbl_status.setText(
            f"Готово: {len(results)} ячеек, успешных отправок: {ok}. "
            "Подробности — на вкладке «Журнал».")
        self.reload()

    def _on_run_err(self, msg: str) -> None:
        self._set_running(False)
        self.lbl_status.setStyleSheet("color: #b00;")
        self.lbl_status.setText("Ошибка запуска: " + msg)

    def _show_result(self, result: RunResult) -> None:
        good = result.status in (RUN_OK, RUN_RESEND_OK)
        self.lbl_status.setStyleSheet("color: green;" if good else "color: #b00;")
        self.lbl_status.setText(f"[{result.status}] {result.message}")


# Worker-thread jobs: each opens its OWN Database (DAL is thread-affine).
def _run_one(ctx: AppContext, cell_id: int) -> RunResult:
    db = ctx.new_db()
    try:
        return CellRunner(db, ctx.work_dir).run_cell(cell_id)
    finally:
        db.close()


def _run_all(ctx: AppContext) -> list[RunResult]:
    db = ctx.new_db()
    try:
        return CellRunner(db, ctx.work_dir).run_all_enabled()
    finally:
        db.close()
