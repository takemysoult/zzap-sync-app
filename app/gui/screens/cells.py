"""Cells screen — the table of upload cells, the duplicate-risk banner, and Run now.

Add/edit/delete cells (via :class:`CellEditor`), see the duplicate-across-warehouses
warning (:func:`find_duplicate_risks`), and run a cell — or all enabled cells — now.
A run that would actually POST to ZZap (global staging OFF *and* the cell's staging
OFF) is confirmed first; the actual staging gate is enforced by CellRunner.

"Run now" opens its OWN ``Database`` inside the worker thread (the DAL is
thread-affine) and runs CellRunner there, off the UI thread.
"""
from __future__ import annotations

from PySide2.QtCore import Qt
from PySide2.QtWidgets import (QAbstractItemView, QDialog, QHBoxLayout, QHeaderView,
                               QLabel, QMessageBox, QPushButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from ...db.models import Cell
from ...services.cell_runner import (RUN_ERROR, RUN_OK, RUN_RESEND_OK, CellRunner,
                                     PreviewResult, RunResult)
from ...services.duplicates import SHARED_WAREHOUSE, find_duplicate_risks
from ...services.preview import run_preview
from ...services.scheduler import SchedulerService
from .. import theme
from ..context import AppContext
from ..workers import AsyncRunner
from .cell_editor import CellEditor

_USER_ROLE = int(Qt.UserRole)


class CellsScreen(QWidget):
    def __init__(self, ctx: AppContext, runner: AsyncRunner,
                 scheduler_service: SchedulerService | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.runner = runner
        self._service = scheduler_service
        self._build()
        self.reload()

    def _build(self) -> None:
        root = QVBoxLayout(self)

        self.banner = QLabel("")
        self.banner.setWordWrap(True)
        self.banner.setProperty("role", "banner")
        self.banner.setVisible(False)
        root.addWidget(self.banner)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Название", "Вкл.", "Куда", "code_templ", "Вид цены", "Склады"])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(
            5, QHeaderView.Stretch)
        self.table.doubleClicked.connect(lambda *_: self._edit())
        root.addWidget(self.table, 1)

        btns = QHBoxLayout()
        b_add = QPushButton("Добавить…")
        b_add.clicked.connect(self._add)
        b_edit = QPushButton("Изменить…")
        b_edit.clicked.connect(self._edit)
        b_del = QPushButton("Удалить")
        b_del.clicked.connect(self._delete)
        self.btn_retry = QPushButton("Дослать отложенное")
        self.btn_retry.clicked.connect(self._retry_selected)
        self.btn_preview = QPushButton("Собрать файл без отправки")
        self.btn_preview.setToolTip(
            "Проверка: собрать XLSX из 1С и показать статистику. "
            "В ZZap НИЧЕГО не отправляется.")
        self.btn_preview.clicked.connect(self._preview_selected)
        self.btn_run = QPushButton("Запустить выбранную")
        self.btn_run.clicked.connect(self._run_selected)
        self.btn_run_all = QPushButton("Запустить все включённые")
        self.btn_run_all.setProperty("class", "primary")
        self.btn_run_all.clicked.connect(self._run_all)
        for b in (b_add, b_edit, b_del):
            btns.addWidget(b)
        btns.addStretch(1)
        btns.addWidget(self.btn_preview)
        btns.addWidget(self.btn_retry)
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
            if cell.target == "email":
                where = "Почта → " + (cell.email_to or "—")
                templ = "—"
            else:
                where = "ZZap: " + (cabinets.get(cell.cabinet_id) or "—")
                templ = str(cell.code_templ)
            values = [
                cell.name,
                "да" if cell.enabled else "нет",
                where,
                templ,
                cell.price_type,
                ", ".join(cell.warehouses),
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
        head = ("Возможное задвоение товара в кабинете ZZap "
                f"({len(risks)} предупреждений). ")
        self.banner.setText(head + "Наведите для подробностей.")
        self.banner.setToolTip("\n\n".join(r.message for r in risks))
        self.banner.setProperty("severity", "high" if high else "split")
        theme.repolish(self.banner)
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
        if dlg.exec_() == QDialog.Accepted:
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
        if dlg.exec_() == QDialog.Accepted:
            self.ctx.db.update_cell(dlg.result_cell())
            self.reload()

    def _delete(self) -> None:
        cell_id = self._selected_id()
        if cell_id is None:
            return
        if QMessageBox.question(self, "Удалить ячейку?",
                                "Удалить выбранную ячейку?") \
                == QMessageBox.Yes:
            self.ctx.db.delete_cell(cell_id)
            self.reload()

    # --- run -------------------------------------------------------------
    def _run_selected(self) -> None:
        cell_id = self._selected_id()
        if cell_id is None:
            return
        cell = self.ctx.db.get_cell(cell_id)
        if cell is None:
            return
        if cell.target == "email":
            detail = (f"Прайс ячейки «{cell.name}» будет отправлен на почту: "
                      f"{cell.email_to or '—'}.")
        else:
            detail = (f"Ячейка «{cell.name}» будет отправлена в ZZap (шаблон "
                      f"{cell.code_templ} будет заменён).")
        if not self._confirm_real(detail):
            return
        self._set_running(True, f"Выполняю ячейку «{cell.name}»…")
        self.runner.submit(lambda: self._do_run_one(cell_id),
                           self._on_run_done, self._on_run_err)

    def _run_all(self) -> None:
        cells = self.ctx.db.list_cells(enabled_only=True)
        if not cells:
            QMessageBox.information(self, "Нет ячеек",
                                   "Нет включённых ячеек для запуска.")
            return
        if not self._confirm_real(
                f"Будут выполнены включённые ячейки: {len(cells)} "
                "(отправка в ZZap и/или на почту)."):
            return
        self._set_running(True, f"Выполняю включённые ячейки ({len(cells)})…")
        self.runner.submit(self._do_run_all,
                           self._on_run_all_done, self._on_run_err)

    def _retry_selected(self) -> None:
        cell_id = self._selected_id()
        if cell_id is None:
            return
        self._set_running(True, "Досылаю отложенное…")
        self.runner.submit(lambda: self._do_retry(cell_id),
                           self._on_retry_done, self._on_run_err)

    # --- preview (build only, send nothing) ------------------------------
    def _preview_selected(self) -> None:
        cell_id = self._selected_id()
        if cell_id is None:
            return
        cell = self.ctx.db.get_cell(cell_id)
        if cell is None:
            return
        # Никаких подтверждений: ничего не отправляется.
        self._set_running(True, f"Собираю файл ячейки «{cell.name}» (без отправки)…")
        self.runner.submit(lambda: self._do_preview(cell_id),
                           self._on_preview_done, self._on_run_err)

    # --- worker-thread jobs (run off the UI thread via AsyncRunner) -------
    # A manual run goes through the scheduler so it executes in the SAME short-lived
    # CHILD PROCESS as scheduled runs — the GUI never loads the 1C COM runtime (~400 МБ)
    # and stays light. Without a scheduler (smoke tests) it falls back to in-process.
    def _do_run_one(self, cell_id: int) -> RunResult:
        if self._service is not None:
            summary = self._service.run_cell_now(cell_id)
            return summary.results[0] if summary.results else RunResult(
                cell_id=cell_id, status=RUN_ERROR,
                message="Выгрузка не вернула результата (см. журнал).")
        return _run_one_inproc(self.ctx, cell_id)

    def _do_run_all(self) -> list[RunResult]:
        if self._service is not None:
            return self._service.run_all_now().results
        return _run_all_inproc(self.ctx)

    def _do_preview(self, cell_id: int) -> PreviewResult:
        # Like a real run, the preview reads 1C — so it must happen in the child process
        # (COM must never load into the long-lived GUI) and must be serialised against
        # scheduled ticks. Without a scheduler (smoke tests) it falls back to in-process.
        if self._service is not None:
            return self._service.run_under_lock(
                lambda: run_preview(cell_id, self.ctx.work_dir))
        return _preview_inproc(self.ctx, cell_id)

    def _do_retry(self, cell_id: int) -> RunResult | None:
        # Retry only re-POSTs an already-built pending file (no 1C COM), so it stays
        # in-process — but still serialised against scheduled ticks via the run-lock.
        if self._service is not None:
            return self._service.run_under_lock(lambda: _retry_inproc(self.ctx, cell_id))
        return _retry_inproc(self.ctx, cell_id)

    def _confirm_real(self, detail: str) -> bool:
        return QMessageBox.warning(
            self, "Подтверждение отправки",
            detail + "\n\nПродолжить отправку?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No) == QMessageBox.Yes

    def _set_running(self, running: bool, message: str = "") -> None:
        self.btn_run.setEnabled(not running)
        self.btn_run_all.setEnabled(not running)
        self.btn_retry.setEnabled(not running)
        self.btn_preview.setEnabled(not running)
        theme.set_status(self.lbl_status, message, "info")

    def _on_preview_done(self, result: PreviewResult) -> None:
        self._set_running(False)
        if not result.ok:
            theme.set_status(self.lbl_status, "Проверка не удалась: " + result.message,
                             "error")
            QMessageBox.warning(self, "Проверка не удалась", result.message)
            return

        text, suspicious = _preview_text(result)
        theme.set_status(
            self.lbl_status,
            f"Проверка: строк {result.rows}, файл собран, в ZZap ничего не отправлено.",
            "error" if suspicious else "ok")
        if suspicious:
            QMessageBox.warning(self, "Проверка: данные выглядят неверными", text)
        else:
            QMessageBox.information(self, "Проверка выполнена", text)

    def _on_run_done(self, result: RunResult) -> None:
        self._set_running(False)
        self._show_result(result)
        self.reload()

    def _on_retry_done(self, result: RunResult | None) -> None:
        self._set_running(False)
        if result is None:
            theme.set_status(self.lbl_status,
                             "Нет отложенных файлов для досылки.", "")
        else:
            self._show_result(result)
        self.reload()

    def _on_run_all_done(self, results: list[RunResult]) -> None:
        self._set_running(False)
        ok = sum(1 for r in results if r.status in (RUN_OK, RUN_RESEND_OK))
        theme.set_status(
            self.lbl_status,
            f"Готово: {len(results)} ячеек, успешных отправок: {ok}. "
            "Подробности — на вкладке «Журнал».", "")
        self.reload()

    def _on_run_err(self, msg: str) -> None:
        self._set_running(False)
        theme.set_status(self.lbl_status, "Ошибка запуска: " + msg, "error")

    def _show_result(self, result: RunResult) -> None:
        good = result.status in (RUN_OK, RUN_RESEND_OK)
        theme.set_status(self.lbl_status, f"[{result.status}] {result.message}",
                         "ok" if good else "error")


def _preview_text(result: PreviewResult) -> tuple[str, bool]:
    """Human-readable preview summary + whether the data looks broken.

    «Suspicious» means every row has a zero quantity or every row has a zero price —
    the signature of a wrong OData field name. Such a file MUST NOT be uploaded: the
    rows exist, so the 0-row guard would not stop it from wiping the ZZap template.
    """
    rows = result.rows
    suspicious = rows > 0 and (result.zero_quantity == rows or result.zero_price == rows)

    lines: list[str] = []
    if suspicious:
        broken = ("количество" if result.zero_quantity == rows else "цена")
        lines.append(
            f"⚠ У ВСЕХ {rows} строк нулевое поле «{broken}».\n"
            "Скорее всего, в запросе OData неверно указано имя поля "
            "(например, остаток называется «ВНаличииBalance», а не «КоличествоBalance»).\n"
            "Такой файл отправлять НЕЛЬЗЯ — он затрёт шаблон ZZap нулями.\n")
    elif rows == 0:
        lines.append("⚠ Получено 0 строк — отправка была бы отклонена защитой.\n")

    lines.append(f"Строк в файле: {rows}")
    lines.append(f"из них с нулевым количеством: {result.zero_quantity}")
    lines.append(f"из них с нулевой ценой: {result.zero_price}")
    lines.append(f"\nФайл: {result.file_path}")
    lines.append("\nВ ZZap ничего не отправлено.")

    if result.sample:
        lines.append("\nПервые строки (производитель | номер | наименование | кол-во | цена):")
        for producer, number, name, qty, price in result.sample[:5]:
            lines.append(f"  {producer or '—'} | {number} | {name} | {qty} | {price}")
    return "\n".join(lines), suspicious


# In-process fallbacks (used only when no scheduler is wired, e.g. smoke tests). Each
# opens its OWN Database (DAL is thread-affine). Production manual runs go through the
# scheduler's child process instead — see CellsScreen._do_run_one/_do_run_all.
def _preview_inproc(ctx: AppContext, cell_id: int) -> PreviewResult:
    db = ctx.new_db()
    try:
        return CellRunner(db, ctx.work_dir).build_preview(cell_id)
    finally:
        db.close()


def _run_one_inproc(ctx: AppContext, cell_id: int) -> RunResult:
    db = ctx.new_db()
    try:
        return CellRunner(db, ctx.work_dir).run_cell(cell_id)
    finally:
        db.close()


def _retry_inproc(ctx: AppContext, cell_id: int) -> RunResult | None:
    db = ctx.new_db()
    try:
        return CellRunner(db, ctx.work_dir).retry_pending(cell_id)
    finally:
        db.close()


def _run_all_inproc(ctx: AppContext) -> list[RunResult]:
    db = ctx.new_db()
    try:
        return CellRunner(db, ctx.work_dir).run_all_enabled()
    finally:
        db.close()
