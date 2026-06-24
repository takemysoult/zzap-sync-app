"""Cell editor dialog — add/edit one upload cell.

Drives the service layer only:
  - warehouse / price-type dropdowns are filled live from 1C via
    ``ConnectionManager.discover`` (off the UI thread; result cached on AppContext);
  - the exclusions importer calls ``read_exclusion_articles`` →
    ``Database.import_exclusion_list`` (the cell carries the resulting list id).

New cells default to **staging** and **disabled** (safety): the file is built but
nothing is sent until the user explicitly opts out and confirms.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMessageBox, QPlainTextEdit, QPushButton, QSpinBox,
                               QVBoxLayout, QWidget)

from ...db.models import Cell
from ...services.connection import ConnectionManager, Discovery
from .. import theme
from ..context import AppContext
from ..workers import AsyncRunner

_CHECKLIST = (
    "Проверьте настройку шаблона в кабинете ZZap (через API не задаётся):\n"
    "• Тип = «Загрузка прайса через API»\n"
    "• Колонки: 1=Производитель, 2=Номер, 3=Наименование, 4=Количество, 5=Цена\n"
    "• Данные с 1-й строки (без заголовка)"
)
_USER_ROLE = int(Qt.ItemDataRole.UserRole)


class ExclusionImportDialog(QDialog):
    """Pick an Excel file + column for an exclusions import."""

    def __init__(self, has_existing: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Импорт исключений из Excel")
        form = QFormLayout(self)
        path_row = QWidget()
        path_l = QHBoxLayout(path_row)
        path_l.setContentsMargins(0, 0, 0, 0)
        self.ed_path = QLineEdit()
        b = QPushButton("Обзор…")
        b.clicked.connect(self._browse)
        path_l.addWidget(self.ed_path, 1)
        path_l.addWidget(b)
        self.sp_col = QSpinBox()
        self.sp_col.setRange(1, 1000)
        self.sp_col.setValue(1)
        self.cb_header = QCheckBox("Первая строка — заголовок (пропустить)")
        self.cb_append = QCheckBox("Добавить к существующему списку")
        self.cb_append.setEnabled(has_existing)
        form.addRow("Файл (.xlsx / .xlsm / .xls)", path_row)
        form.addRow("Колонка с артикулом", self.sp_col)
        form.addRow(self.cb_header)
        form.addRow(self.cb_append)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setProperty("class", "primary")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Выберите Excel-файл", filter="Excel (*.xlsx *.xlsm *.xls)")
        if path:
            self.ed_path.setText(path)

    def _accept(self) -> None:
        if not self.ed_path.text().strip():
            QMessageBox.warning(self, "Файл не выбран", "Укажите Excel-файл.")
            return
        self.accept()

    def values(self) -> tuple[str, int, bool, bool]:
        # column shown 1-based; read_exclusion_articles wants 0-based.
        return (self.ed_path.text().strip(), self.sp_col.value() - 1,
                self.cb_header.isChecked(), self.cb_append.isChecked())


class CellEditor(QDialog):
    def __init__(self, ctx: AppContext, runner: AsyncRunner, cell: Cell | None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.runner = runner
        self.setWindowTitle("Ячейка")
        self.resize(560, 640)
        self._cell = cell or Cell()  # Cell() carries the safe defaults (staging on)
        self._exclusion_list_id = self._cell.exclusion_list_id
        self._build()
        self._load(self._cell)

    # --- build -----------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        form = QFormLayout()

        self.ed_name = QLineEdit()
        self.cb_enabled = QCheckBox("Ячейка включена (участвует в авто-выгрузке)")
        self.cmb_cabinet = QComboBox()
        self.cmb_conn = QComboBox()
        self.sp_templ = QSpinBox()
        self.sp_templ.setRange(0, 2_147_483_647)
        self.sp_templ.setGroupSeparatorShown(False)
        # Non-editable: a click opens the list so the user picks a discovered price
        # type (free text only invites typos that yield 0 rows). A saved/offline value
        # is always kept in the list by _populate_price_types so nothing is lost.
        self.cmb_price = QComboBox()
        self.cmb_price.setPlaceholderText("— загрузите из 1С и выберите —")

        form.addRow("Название", self.ed_name)
        form.addRow("", self.cb_enabled)
        form.addRow("Кабинет ZZap", self.cmb_cabinet)
        form.addRow("Подключение 1С", self.cmb_conn)
        form.addRow("Код шаблона (code_templ)", self.sp_templ)
        form.addRow("Вид цены", self.cmb_price)
        root.addLayout(form)

        # Warehouses (multi-select) + load-from-1C
        wh_box = QGroupBox("Склады (можно выбрать несколько)")
        wh_l = QVBoxLayout(wh_box)
        self.lst_wh = QListWidget()
        wh_l.addWidget(self.lst_wh)
        load_row = QHBoxLayout()
        self.btn_discover = QPushButton("Загрузить склады и виды цен из 1С")
        self.btn_discover.clicked.connect(self._discover)
        self.lbl_discover = QLabel("")
        self.lbl_discover.setProperty("role", "hint")
        load_row.addWidget(self.btn_discover)
        load_row.addWidget(self.lbl_discover, 1)
        wh_l.addLayout(load_row)
        root.addWidget(wh_box)

        # Exclusions
        exc_box = QGroupBox("Исключения (артикулы, которые не выгружать)")
        exc_l = QVBoxLayout(exc_box)
        self.lbl_exc = QLabel("")
        exc_l.addWidget(self.lbl_exc)
        exc_btns = QHBoxLayout()
        b_import = QPushButton("Импорт из Excel…")
        b_import.clicked.connect(self._import_exclusions)
        b_edit = QPushButton("Редактировать…")
        b_edit.clicked.connect(self._edit_exclusions)
        b_clear = QPushButton("Очистить")
        b_clear.clicked.connect(self._clear_exclusions)
        for b in (b_import, b_edit, b_clear):
            exc_btns.addWidget(b)
        exc_btns.addStretch(1)
        exc_l.addLayout(exc_btns)
        root.addWidget(exc_box)

        checklist = QLabel(_CHECKLIST)
        checklist.setWordWrap(True)
        checklist.setProperty("role", "checklist")
        root.addWidget(checklist)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setProperty("class", "primary")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    # --- load ------------------------------------------------------------
    def _load(self, cell: Cell) -> None:
        self.ed_name.setText(cell.name)
        self.cb_enabled.setChecked(cell.enabled)
        self.sp_templ.setValue(int(cell.code_templ or 0))

        self.cmb_cabinet.clear()
        self.cmb_cabinet.addItem("— не выбран —", None)
        for cab in self.ctx.db.list_cabinets():
            self.cmb_cabinet.addItem(cab.name, cab.id)
        self._select_data(self.cmb_cabinet, cell.cabinet_id)

        self.cmb_conn.clear()
        self.cmb_conn.addItem("— не выбрано —", None)
        conns = self.ctx.db.list_connections()
        for c in conns:
            self.cmb_conn.addItem(c.name, c.id)
        default_conn_id = cell.connection_id
        if default_conn_id is None:
            default = self.ctx.db.get_default_connection()
            default_conn_id = default.id if default else None
        self._select_data(self.cmb_conn, default_conn_id)

        # price types + warehouses from cached discovery (if any), merged with the
        # cell's own saved values so nothing is lost when 1C is offline.
        disc = self.ctx.discovery
        price_types = list(disc.price_types) if disc else []
        warehouses = list(disc.warehouses) if disc else []
        self._populate_price_types(price_types, current=cell.price_type)
        self._populate_warehouses(warehouses, checked=cell.warehouses)
        self._refresh_exclusions_label()

    @staticmethod
    def _select_data(combo: QComboBox, value) -> None:
        idx = combo.findData(value)
        combo.setCurrentIndex(idx if idx >= 0 else 0)

    def _populate_price_types(self, price_types: list[str], current: str) -> None:
        """Fill the price-type dropdown from discovery, keeping the saved value.

        The current/saved value is always included (so an offline or previously-chosen
        type isn't lost) and re-selected; with no saved value the box stays unselected
        so the user makes a deliberate pick from the loaded list.
        """
        current = (current or "").strip()
        self.cmb_price.blockSignals(True)
        self.cmb_price.clear()
        seen: list[str] = []
        for p in list(price_types) + ([current] if current else []):
            p = (p or "").strip()
            if p and p not in seen:
                seen.append(p)
        self.cmb_price.addItems(seen)
        self.cmb_price.setCurrentIndex(self.cmb_price.findText(current) if current else -1)
        self.cmb_price.blockSignals(False)

    def _populate_warehouses(self, warehouses: list[str], checked: list[str]) -> None:
        checked_norm = {w.strip().casefold() for w in checked}
        ordered: list[str] = []
        seen: set[str] = set()
        for w in list(warehouses) + list(checked):  # discovery first, then any extras
            key = w.strip().casefold()
            if w.strip() and key not in seen:
                seen.add(key)
                ordered.append(w.strip())
        self.lst_wh.clear()
        for w in ordered:
            item = QListWidgetItem(w)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked
                               if w.strip().casefold() in checked_norm
                               else Qt.CheckState.Unchecked)
            self.lst_wh.addItem(item)

    def _checked_warehouses(self) -> list[str]:
        return [self.lst_wh.item(i).text()
                for i in range(self.lst_wh.count())
                if self.lst_wh.item(i).checkState() == Qt.CheckState.Checked]

    # --- discovery -------------------------------------------------------
    def _discover(self) -> None:
        conn_id = self.cmb_conn.currentData()
        if conn_id is None:
            QMessageBox.warning(self, "Нет подключения",
                                "Сначала выберите подключение 1С (и сохраните его на "
                                "вкладке «Подключение 1С»).")
            return
        conn = self.ctx.db.get_connection(conn_id)
        if conn is None:
            return
        password = self.ctx.db.get_connection_password(conn_id)
        self.btn_discover.setEnabled(False)
        self.lbl_discover.setText("Запрашиваю склады и виды цен у 1С…")

        def job() -> Discovery:
            return ConnectionManager().discover(conn, password)

        self.runner.submit(job, self._on_discover_ok, self._on_discover_err)

    def _on_discover_ok(self, disc: Discovery) -> None:
        self.btn_discover.setEnabled(True)
        self.ctx.discovery = disc
        current_price = self.cmb_price.currentText()
        checked = self._checked_warehouses()
        self._populate_price_types(disc.price_types, current=current_price)
        self._populate_warehouses(disc.warehouses, checked=checked)
        self.lbl_discover.setText(
            f"Найдено: складов {len(disc.warehouses)}, видов цен {len(disc.price_types)}.")

    def _on_discover_err(self, msg: str) -> None:
        self.btn_discover.setEnabled(True)
        self.lbl_discover.setText("Ошибка запроса к 1С.")
        QMessageBox.warning(self, "Не удалось получить данные из 1С", msg)

    # --- exclusions ------------------------------------------------------
    def _refresh_exclusions_label(self) -> None:
        if not self._exclusion_list_id:
            self.lbl_exc.setText("Список исключений не задан.")
            return
        lst = self.ctx.db.get_exclusion_list(self._exclusion_list_id)
        if lst is None:
            self._exclusion_list_id = None
            self.lbl_exc.setText("Список исключений не задан.")
            return
        count = len([ln for ln in lst.articles.splitlines()
                     if ln.strip() and not ln.strip().startswith("#")])
        self.lbl_exc.setText(f"Список «{lst.name}» — артикулов: {count}.")

    def _import_exclusions(self) -> None:
        dlg = ExclusionImportDialog(has_existing=bool(self._exclusion_list_id), parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        path, column, skip_header, append = dlg.values()
        from engine.exclusions import read_exclusion_articles
        try:
            articles = read_exclusion_articles(path, column=column, skip_header=skip_header)
        except Exception as e:  # noqa: BLE001 - bad file/column → show, don't crash
            QMessageBox.warning(self, "Ошибка импорта", str(e))
            return
        if not articles:
            QMessageBox.information(self, "Пусто",
                                   "В выбранной колонке не найдено артикулов.")
            return
        name = f"Исключения: {self.ed_name.text().strip() or 'ячейка'}"
        self._exclusion_list_id = self.ctx.db.import_exclusion_list(
            articles, name=name, list_id=self._exclusion_list_id, append=append)
        self._refresh_exclusions_label()
        QMessageBox.information(self, "Импорт выполнен",
                               f"Прочитано артикулов из файла: {len(articles)}.")

    def _edit_exclusions(self) -> None:
        existing = (self.ctx.db.get_exclusion_list(self._exclusion_list_id)
                    if self._exclusion_list_id else None)
        dlg = QDialog(self)
        dlg.setWindowTitle("Список исключений")
        dlg.resize(360, 480)
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel("Один артикул в строке; строка с «#» — комментарий."))
        editor = QPlainTextEdit(existing.articles if existing else "")
        lay.addWidget(editor)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setProperty("class", "primary")
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        lay.addWidget(buttons)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        text = editor.toPlainText()
        if existing is not None:
            existing.articles = text
            self.ctx.db.update_exclusion_list(existing)
        else:
            name = f"Исключения: {self.ed_name.text().strip() or 'ячейка'}"
            self._exclusion_list_id = self.ctx.db.add_exclusion_list(name, text)
        self._refresh_exclusions_label()

    def _clear_exclusions(self) -> None:
        # Unassign only — the list itself is kept (it may be shared / reused).
        self._exclusion_list_id = None
        self._refresh_exclusions_label()

    # --- accept ----------------------------------------------------------
    def _accept(self) -> None:
        if not self.ed_name.text().strip():
            QMessageBox.warning(self, "Проверьте поля", "Укажите название ячейки.")
            return
        if not self._checked_warehouses():
            QMessageBox.warning(self, "Проверьте поля",
                                "Выберите хотя бы один склад.")
            return
        if not self.cmb_price.currentText().strip():
            QMessageBox.warning(self, "Проверьте поля", "Укажите вид цены.")
            return
        self.accept()

    def result_cell(self) -> Cell:
        c = self._cell
        return Cell(
            id=c.id,
            name=self.ed_name.text().strip(),
            enabled=self.cb_enabled.isChecked(),
            connection_id=self.cmb_conn.currentData(),
            cabinet_id=self.cmb_cabinet.currentData(),
            code_templ=int(self.sp_templ.value()),
            price_type=self.cmb_price.currentText().strip(),
            warehouses=self._checked_warehouses(),
            exclusion_list_id=self._exclusion_list_id,
            include_header=c.include_header,
            columns=c.columns,
        )
