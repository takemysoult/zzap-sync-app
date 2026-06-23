"""Connection screen — enter 1C credentials, test the connection, save (encrypted).

Manages a single *default* 1C connection (the MVP source; multi-connection UX is
Phase 7). The password is DPAPI-encrypted by the DAL and is NEVER loaded back into
the field — a placeholder shows that a secret is stored; the stored password is only
replaced when the user types a new one. The "Test connection" call runs off the UI
thread (1C COM must not block the UI).
"""
from __future__ import annotations

from PySide6.QtWidgets import (QButtonGroup, QFileDialog, QFormLayout, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                               QPushButton, QRadioButton, QVBoxLayout, QWidget)

from ...db.models import Connection1C
from ...services.connection import ConnectionManager, ConnectionResult
from .. import theme
from ..context import AppContext
from ..workers import AsyncRunner

_SAVED_HINT = "•••••••• (сохранён — оставьте пустым, чтобы не менять)"


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

        kind_box = QGroupBox("Тип базы 1С")
        kind_l = QHBoxLayout(kind_box)
        self.rb_server = QRadioButton("Серверная база")
        self.rb_file = QRadioButton("Файловая база")
        self.rb_server.setChecked(True)
        self._kind_group = QButtonGroup(self)
        self._kind_group.addButton(self.rb_server)
        self._kind_group.addButton(self.rb_file)
        kind_l.addWidget(self.rb_server)
        kind_l.addWidget(self.rb_file)
        kind_l.addStretch(1)
        root.addWidget(kind_box)

        form_box = QGroupBox("Параметры подключения")
        form = QFormLayout(form_box)
        self.ed_name = QLineEdit()
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
        self.ed_usr = QLineEdit()
        self.ed_pwd = QLineEdit()
        self.ed_pwd.setEchoMode(QLineEdit.EchoMode.Password)

        form.addRow("Название", self.ed_name)
        form.addRow("Сервер (Srvr)", self.ed_srvr)
        form.addRow("База (Ref)", self.ed_ref)
        form.addRow("Файл базы", file_row)
        form.addRow("ProgID коннектора", self.ed_progid)
        form.addRow("Пользователь 1С", self.ed_usr)
        form.addRow("Пароль", self.ed_pwd)
        root.addWidget(form_box)

        hint = QLabel("Пользователю 1С нужно право «Внешнее соединение» (COM).")
        hint.setWordWrap(True)
        hint.setProperty("role", "hint")
        root.addWidget(hint)

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

        self.rb_server.toggled.connect(self._sync_kind_fields)
        self._sync_kind_fields()

    def _sync_kind_fields(self) -> None:
        server = self.rb_server.isChecked()
        self.ed_srvr.setEnabled(server)
        self.ed_ref.setEnabled(server)
        self.ed_file.setEnabled(not server)
        self.btn_browse.setEnabled(not server)

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
            return
        self._conn_id = conn.id
        self.ed_name.setText(conn.name)
        self.rb_server.setChecked(conn.kind != "file")
        self.rb_file.setChecked(conn.kind == "file")
        self.ed_srvr.setText(conn.srvr)
        self.ed_ref.setText(conn.ref)
        self.ed_file.setText(conn.file_path)
        self.ed_progid.setText(conn.progid or "V83.COMConnector")
        self.ed_usr.setText(conn.usr)
        self.ed_pwd.setPlaceholderText(_SAVED_HINT if conn.has_password else "")
        self._sync_kind_fields()

    def _read_form(self) -> Connection1C:
        return Connection1C(
            id=self._conn_id,
            name=self.ed_name.text().strip() or "Основное подключение",
            kind="server" if self.rb_server.isChecked() else "file",
            srvr=self.ed_srvr.text().strip(),
            ref=self.ed_ref.text().strip(),
            file_path=self.ed_file.text().strip(),
            progid=self.ed_progid.text().strip() or "V83.COMConnector",
            usr=self.ed_usr.text().strip(),
            is_default=True,
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
        if conn.kind == "server" and (not conn.srvr or not conn.ref):
            QMessageBox.warning(self, "Проверьте поля",
                                "Для серверной базы укажите сервер (Srvr) и базу (Ref).")
            return
        if conn.kind == "file" and not conn.file_path:
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
