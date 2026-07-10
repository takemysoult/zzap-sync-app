"""Connection screen — enter 1C credentials, test the connection, save (encrypted).

Manages a single *default* 1C connection (the MVP source; multi-connection UX is
Phase 7). The password is DPAPI-encrypted by the DAL and is NEVER loaded back into
the field — a placeholder shows that a secret is stored; the stored password is only
replaced when the user types a new one. The "Test connection" call runs off the UI
thread (1C COM must not block the UI).
"""
from __future__ import annotations

from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QFileDialog, QFormLayout,
                               QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                               QMessageBox, QPlainTextEdit, QPushButton,
                               QRadioButton, QVBoxLayout, QWidget)

from ...db.models import Connection1C
from ...services.connection import ConnectionManager, ConnectionResult
from .. import theme
from ..context import AppContext
from ..workers import AsyncRunner

_SAVED_HINT = "•••••••• (сохранён — оставьте пустым, чтобы не менять)"


def _query_edit(placeholder: str) -> QPlainTextEdit:
    """A short multi-line editor for one OData query (kept compact in the form)."""
    ed = QPlainTextEdit()
    ed.setPlaceholderText(placeholder)
    ed.setMaximumHeight(56)
    ed.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
    return ed


class ConnectionScreen(QWidget):
    def __init__(self, ctx: AppContext, runner: AsyncRunner,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.runner = runner
        self._conn_id: int | None = None
        self._build()
        self.reload()

    # --- build -----------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)

        kind_box = QGroupBox("Способ подключения к 1С")
        kind_l = QHBoxLayout(kind_box)
        self.rb_server = QRadioButton("Серверная база (COM)")
        self.rb_file = QRadioButton("Файловая база (COM)")
        self.rb_odata = QRadioButton("OData (HTTP)")
        self.rb_server.setChecked(True)
        self._kind_group = QButtonGroup(self)
        for rb in (self.rb_server, self.rb_file, self.rb_odata):
            self._kind_group.addButton(rb)
            kind_l.addWidget(rb)
        kind_l.addStretch(1)
        root.addWidget(kind_box)

        # Name (shared by all connection types).
        name_form = QFormLayout()
        self.ed_name = QLineEdit()
        name_form.addRow("Название", self.ed_name)
        root.addLayout(name_form)

        # --- COM parameters ---
        self.com_box = QGroupBox("Параметры 1С (COM)")
        com_form = QFormLayout(self.com_box)
        self.ed_srvr = QLineEdit()
        self.ed_srvr.setPlaceholderText("например, 192.168.55.34")
        self.ed_ref = QLineEdit()
        self.ed_ref.setPlaceholderText("например, ut2025")
        file_row = QWidget()
        file_l = QHBoxLayout(file_row)
        file_l.setContentsMargins(0, 0, 0, 0)
        self.ed_file = QLineEdit()
        self.btn_browse = QPushButton("Обзор…")
        self.btn_browse.clicked.connect(self._browse_file)
        file_l.addWidget(self.ed_file, 1)
        file_l.addWidget(self.btn_browse)
        self.ed_progid = QLineEdit("V83.COMConnector")
        com_form.addRow("Сервер (Srvr)", self.ed_srvr)
        com_form.addRow("База (Ref)", self.ed_ref)
        com_form.addRow("Файл базы", file_row)
        com_form.addRow("ProgID коннектора", self.ed_progid)
        root.addWidget(self.com_box)

        # --- OData parameters ---
        self.odata_box = QGroupBox("Параметры OData")
        od_form = QFormLayout(self.odata_box)
        self.ed_base = QLineEdit()
        self.ed_base.setPlaceholderText(
            "http://адрес/база/odata/standard.odata")
        self.ed_q_nom = _query_edit(
            "Catalog_Номенклатура?$select=Ref_Key,Артикул,Description,"
            "Производитель_Key&$filter=DeletionMark eq false and IsFolder eq false")
        self.ed_q_price = _query_edit(
            "InformationRegister_ЦеныНоменклатуры_SliceLast?"
            "$select=Номенклатура_Key,Цена")
        self.ed_q_stock = _query_edit(
            "AccumulationRegister_ТоварыНаСкладах_Balance?"
            "$select=Номенклатура_Key,КоличествоBalance")
        self.ed_q_prod = _query_edit(
            "Catalog_Производители?$select=Ref_Key,Description")
        self.cb_verify = QCheckBox(
            "Проверять SSL-сертификат сервера (снимите для самоподписанного по VPN)")
        self.cb_verify.setChecked(True)
        od_form.addRow("Адрес OData (base_url)", self.ed_base)
        od_form.addRow("Запрос номенклатуры", self.ed_q_nom)
        od_form.addRow("Запрос цен", self.ed_q_price)
        od_form.addRow("Запрос остатков", self.ed_q_stock)
        od_form.addRow("Запрос производителей", self.ed_q_prod)
        od_form.addRow("", self.cb_verify)
        root.addWidget(self.odata_box)

        # --- Credentials (shared) ---
        creds_box = QGroupBox("Учётные данные")
        creds_form = QFormLayout(creds_box)
        self.ed_usr = QLineEdit()
        self.ed_pwd = QLineEdit()
        self.ed_pwd.setEchoMode(QLineEdit.EchoMode.Password)
        creds_form.addRow("Пользователь", self.ed_usr)
        creds_form.addRow("Пароль", self.ed_pwd)
        root.addWidget(creds_box)

        self.hint_com = QLabel("Пользователю 1С нужно право «Внешнее соединение» (COM).")
        self.hint_odata = QLabel(
            "OData: в базе 1С должен быть опубликован стандартный интерфейс OData, а у "
            "пользователя — право на него. Склады и вид цены задаются прямо в запросах "
            "($filter), поэтому в ячейке их выбирать не нужно.")
        for h in (self.hint_com, self.hint_odata):
            h.setWordWrap(True)
            h.setProperty("role", "hint")
            root.addWidget(h)

        btns = QHBoxLayout()
        self.btn_test = QPushButton("Проверить соединение")
        self.btn_test.clicked.connect(self._on_test)
        self.btn_save = QPushButton("Сохранить")
        self.btn_save.setProperty("class", "primary")
        self.btn_save.clicked.connect(self._on_save)
        btns.addWidget(self.btn_test)
        btns.addWidget(self.btn_save)
        btns.addStretch(1)
        root.addLayout(btns)

        self.lbl_status = QLabel("")
        self.lbl_status.setWordWrap(True)
        root.addWidget(self.lbl_status)
        root.addStretch(1)

        for rb in (self.rb_server, self.rb_file, self.rb_odata):
            rb.toggled.connect(self._sync_kind_fields)
        self._sync_kind_fields()

    def _sync_kind_fields(self) -> None:
        server = self.rb_server.isChecked()
        file = self.rb_file.isChecked()
        odata = self.rb_odata.isChecked()
        com = server or file
        self.com_box.setVisible(com)
        self.odata_box.setVisible(odata)
        self.hint_com.setVisible(com)
        self.hint_odata.setVisible(odata)
        self.ed_srvr.setEnabled(server)
        self.ed_ref.setEnabled(server)
        self.ed_file.setEnabled(file)
        self.btn_browse.setEnabled(file)

    def _browse_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Выберите файл базы 1С (1Cv8.1CD)")
        if path:
            self.ed_file.setText(path)

    # --- load / read -----------------------------------------------------
    def reload(self) -> None:
        conn = self.ctx.db.get_default_connection()
        self.ed_pwd.clear()
        if conn is None:
            self._conn_id = None
            self.ed_name.setText("Основное подключение")
            self.ed_progid.setText("V83.COMConnector")
            self.ed_pwd.setPlaceholderText("")
            self.rb_server.setChecked(True)
            self._sync_kind_fields()
            return
        self._conn_id = conn.id
        self.ed_name.setText(conn.name)
        if conn.source == "odata":
            self.rb_odata.setChecked(True)
        else:
            self.rb_server.setChecked(conn.kind != "file")
            self.rb_file.setChecked(conn.kind == "file")
        self.ed_srvr.setText(conn.srvr)
        self.ed_ref.setText(conn.ref)
        self.ed_file.setText(conn.file_path)
        self.ed_progid.setText(conn.progid or "V83.COMConnector")
        self.ed_base.setText(conn.odata_base_url)
        self.ed_q_nom.setPlainText(conn.odata_nomenclature_query)
        self.ed_q_price.setPlainText(conn.odata_prices_query)
        self.ed_q_stock.setPlainText(conn.odata_stock_query)
        self.ed_q_prod.setPlainText(conn.odata_producers_query)
        self.cb_verify.setChecked(conn.odata_verify_ssl)
        self.ed_usr.setText(conn.usr)
        self.ed_pwd.setPlaceholderText(_SAVED_HINT if conn.has_password else "")
        self._sync_kind_fields()

    def _read_form(self) -> Connection1C:
        odata = self.rb_odata.isChecked()
        return Connection1C(
            id=self._conn_id,
            name=self.ed_name.text().strip() or "Основное подключение",
            kind="file" if self.rb_file.isChecked() else "server",
            srvr=self.ed_srvr.text().strip(),
            ref=self.ed_ref.text().strip(),
            file_path=self.ed_file.text().strip(),
            progid=self.ed_progid.text().strip() or "V83.COMConnector",
            usr=self.ed_usr.text().strip(),
            is_default=True,
            source="odata" if odata else "com",
            odata_base_url=self.ed_base.text().strip(),
            odata_nomenclature_query=self.ed_q_nom.toPlainText().strip(),
            odata_prices_query=self.ed_q_price.toPlainText().strip(),
            odata_stock_query=self.ed_q_stock.toPlainText().strip(),
            odata_producers_query=self.ed_q_prod.toPlainText().strip(),
            odata_verify_ssl=self.cb_verify.isChecked(),
        )

    def _password_for_run(self) -> str | None:
        """Plaintext password to use for a test: the typed value, else the stored one."""
        typed = self.ed_pwd.text()
        if typed:
            return typed
        if self._conn_id is not None:
            return self.ctx.db.get_connection_password(self._conn_id)
        return None

    # --- actions ---------------------------------------------------------
    def _on_test(self) -> None:
        conn = self._read_form()
        password = self._password_for_run()
        self.btn_test.setEnabled(False)
        theme.set_status(self.lbl_status, "Проверяю соединение с 1С…", "info")

        def job() -> ConnectionResult:
            return ConnectionManager().test_connection(conn, password)

        self.runner.submit(job, self._on_test_done, self._on_test_err)

    def _on_test_done(self, result: ConnectionResult) -> None:
        self.btn_test.setEnabled(True)
        if result.ok:
            theme.set_status(self.lbl_status, "✓ " + result.message, "ok")
        else:
            theme.set_status(self.lbl_status, "✗ " + result.message, "error")

    def _on_test_err(self, msg: str) -> None:
        self.btn_test.setEnabled(True)
        theme.set_status(self.lbl_status, "✗ Ошибка проверки: " + msg, "error")

    def _on_save(self) -> None:
        conn = self._read_form()
        if conn.source == "odata":
            if not conn.odata_base_url:
                QMessageBox.warning(self, "Проверьте поля",
                                    "Укажите адрес OData-сервиса (base_url).")
                return
            if not conn.odata_nomenclature_query:
                QMessageBox.warning(self, "Проверьте поля",
                                    "Укажите запрос номенклатуры для OData.")
                return
        elif conn.kind == "server" and (not conn.srvr or not conn.ref):
            QMessageBox.warning(self, "Проверьте поля",
                                "Для серверной базы укажите сервер (Srvr) и базу (Ref).")
            return
        elif conn.kind == "file" and not conn.file_path:
            QMessageBox.warning(self, "Проверьте поля",
                                "Для файловой базы укажите путь к файлу базы.")
            return
        new_password = self.ed_pwd.text()
        update_password = bool(new_password)
        if self._conn_id is None:
            self._conn_id = self.ctx.db.add_connection(conn, new_password or None)
        else:
            self.ctx.db.update_connection(conn, password=new_password or None,
                                          update_password=update_password)
        self.reload()
        theme.set_status(self.lbl_status, "Сохранено.", "ok")
