"""Cabinets screen — manage ZZap accounts (name + masked API key + api_url).

A "cabinet" is one ZZap account = one API key. Cells reference a cabinet. The API
key is DPAPI-encrypted by the DAL, masked in the field, and never loaded back into
the UI — when editing, a placeholder shows a key is stored and it is only replaced
if the user types a new one.

There is no live "test key" button: ZZap has no cheap key-validation method (the
only verified call is the actual upload — PROJECT_MEMORY §4), so a key is proven on
the first real upload.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QDialogButtonBox,
                               QFormLayout, QHBoxLayout, QHeaderView, QLineEdit,
                               QMessageBox, QPushButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from ...db.models import Cabinet
from ..context import AppContext

_DEFAULT_API_URL = "https://b52-api.zzap.pro/api/client/v1/price1c/upload"
_SAVED_HINT = "•••••••• (сохранён — оставьте пустым, чтобы не менять)"
_USER_ROLE = int(Qt.ItemDataRole.UserRole)


class CabinetDialog(QDialog):
    def __init__(self, cabinet: Cabinet | None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Кабинет ZZap")
        self._cabinet = cabinet
        form = QFormLayout(self)
        self.ed_name = QLineEdit(cabinet.name if cabinet else "")
        self.ed_url = QLineEdit((cabinet.api_url if cabinet else "") or _DEFAULT_API_URL)
        self.ed_key = QLineEdit()
        self.ed_key.setEchoMode(QLineEdit.EchoMode.Password)
        if cabinet and cabinet.has_api_key:
            self.ed_key.setPlaceholderText(_SAVED_HINT)
        form.addRow("Название", self.ed_name)
        form.addRow("API-ключ (zzap-api-key)", self.ed_key)
        form.addRow("URL метода", self.ed_url)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _accept(self) -> None:
        if not self.ed_name.text().strip():
            QMessageBox.warning(self, "Проверьте поля", "Укажите название кабинета.")
            return
        self.accept()

    def result_cabinet(self) -> Cabinet:
        return Cabinet(
            id=self._cabinet.id if self._cabinet else None,
            name=self.ed_name.text().strip(),
            api_url=self.ed_url.text().strip() or _DEFAULT_API_URL,
        )

    def new_api_key(self) -> str | None:
        """The typed key (None if left blank = keep the stored one on edit)."""
        return self.ed_key.text() or None


class CabinetsScreen(QWidget):
    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._build()
        self.reload()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Название", "API-ключ", "URL метода"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch)
        self.table.doubleClicked.connect(lambda *_: self._edit())
        root.addWidget(self.table, 1)

        btns = QHBoxLayout()
        b_add = QPushButton("Добавить…")
        b_add.clicked.connect(self._add)
        b_edit = QPushButton("Изменить…")
        b_edit.clicked.connect(self._edit)
        b_del = QPushButton("Удалить")
        b_del.clicked.connect(self._delete)
        for b in (b_add, b_edit, b_del):
            btns.addWidget(b)
        btns.addStretch(1)
        root.addLayout(btns)

    def reload(self) -> None:
        cabinets = self.ctx.db.list_cabinets()
        self.table.setRowCount(len(cabinets))
        for row, cab in enumerate(cabinets):
            key_text = "задан" if cab.has_api_key else "— не задан —"
            values = [cab.name, key_text, cab.api_url]
            for col, text in enumerate(values):
                item = QTableWidgetItem(text)
                if col == 0:
                    item.setData(_USER_ROLE, cab.id)
                self.table.setItem(row, col, item)

    def _selected_id(self) -> int | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        return item.data(_USER_ROLE) if item else None

    def _add(self) -> None:
        dlg = CabinetDialog(None, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.ctx.db.add_cabinet(dlg.result_cabinet(), dlg.new_api_key())
            self.reload()

    def _edit(self) -> None:
        cab_id = self._selected_id()
        if cab_id is None:
            return
        cabinet = self.ctx.db.get_cabinet(cab_id)
        if cabinet is None:
            return
        dlg = CabinetDialog(cabinet, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            key = dlg.new_api_key()
            self.ctx.db.update_cabinet(dlg.result_cabinet(), api_key=key,
                                       update_api_key=key is not None)
            self.reload()

    def _delete(self) -> None:
        cab_id = self._selected_id()
        if cab_id is None:
            return
        if QMessageBox.question(self, "Удалить кабинет?",
                                "Удалить выбранный кабинет ZZap?") \
                == QMessageBox.StandardButton.Yes:
            self.ctx.db.delete_cabinet(cab_id)
            self.reload()
