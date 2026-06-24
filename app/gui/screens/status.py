"""Status / logs screen — run history + the per-cell journal tail.

Read-only monitoring: the ``run_history`` table (most recent runs) and, for a
selected cell, the tail of its ``upload_journal.log`` (written by Delivery under the
cell's work dir). Pure reads — no business logic here.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QHBoxLayout,
                               QHeaderView, QLabel, QPlainTextEdit, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from engine.delivery import Delivery

from .. import theme
from ..context import AppContext

_JOURNAL_TAIL_LINES = 200


class StatusScreen(QWidget):
    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._build()
        self.reload()

    def _build(self) -> None:
        root = QVBoxLayout(self)

        top = QHBoxLayout()
        top.addWidget(QLabel("Ячейка:"))
        self.cmb_cell = QComboBox()
        self.cmb_cell.currentIndexChanged.connect(lambda *_: self._refresh_views())
        top.addWidget(self.cmb_cell, 1)
        b_refresh = QPushButton("Обновить")
        b_refresh.clicked.connect(self.reload)
        top.addWidget(b_refresh)
        root.addLayout(top)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Начало", "Конец", "Статус", "Строк", "Заметка", "Сообщение"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(
            5, QHeaderView.ResizeMode.Stretch)
        root.addWidget(self.table, 2)

        self.lbl_pending = QLabel("")
        self.lbl_pending.setWordWrap(True)
        self.lbl_pending.setProperty("role", "hint")
        self.lbl_pending.setVisible(False)
        root.addWidget(self.lbl_pending)

        root.addWidget(QLabel("Журнал выбранной ячейки:"))
        self.journal = QPlainTextEdit()
        self.journal.setReadOnly(True)
        self.journal.setFont(theme.mono_font())
        root.addWidget(self.journal, 1)

    def reload(self) -> None:
        prev = self.cmb_cell.currentData()
        self.cmb_cell.blockSignals(True)
        self.cmb_cell.clear()
        self.cmb_cell.addItem("Все ячейки", None)
        for cell in self.ctx.db.list_cells():
            self.cmb_cell.addItem(f"{cell.name} (#{cell.id})", cell.id)
        idx = self.cmb_cell.findData(prev)
        self.cmb_cell.setCurrentIndex(idx if idx >= 0 else 0)
        self.cmb_cell.blockSignals(False)
        self._refresh_views()

    def _refresh_views(self) -> None:
        cell_id = self.cmb_cell.currentData()
        runs = self.ctx.db.list_runs(cell_id=cell_id, limit=100)
        self.table.setRowCount(len(runs))
        for row, r in enumerate(runs):
            values = [
                r.started_at or "",
                r.finished_at or "",
                r.status or "",
                "" if r.rows_sent is None else str(r.rows_sent),
                r.rows_note or "",
                r.message or "",
            ]
            for col, text in enumerate(values):
                item = QTableWidgetItem(text)
                if col == 2 and r.status in theme.STATUS_COLORS:  # colour the status cell
                    item.setForeground(theme.color(theme.STATUS_COLORS[r.status]))
                self.table.setItem(row, col, item)
        self._refresh_pending(cell_id)
        self._load_journal(cell_id)

    def _refresh_pending(self, cell_id) -> None:
        """Show whether the selected cell has a file waiting to be re-sent."""
        if cell_id is None:
            self.lbl_pending.setVisible(False)
            return
        pending = Delivery(Path(self.ctx.work_dir) / f"cell_{cell_id}").read_state().get("pending")
        if not pending:
            self.lbl_pending.setVisible(False)
            return
        since = pending.get("since", "?")
        rows = pending.get("rows", "?")
        self.lbl_pending.setText(
            f"⏳ Ожидает досылки с {since} · строк: {rows}. "
            "Уйдёт автоматически при появлении сети или по кнопке «Дослать отложенное».")
        self.lbl_pending.setVisible(True)

    def _load_journal(self, cell_id) -> None:
        if cell_id is None:
            self.journal.setPlainText(
                "Выберите конкретную ячейку, чтобы увидеть её журнал.")
            return
        path = Path(self.ctx.work_dir) / f"cell_{cell_id}" / "upload_journal.log"
        if not path.exists():
            self.journal.setPlainText("(журнал пуст)")
            return
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as e:
            self.journal.setPlainText(f"(не удалось прочитать журнал: {e})")
            return
        self.journal.setPlainText("\n".join(lines[-_JOURNAL_TAIL_LINES:]))
