"""Email accounts screen — «вход в свою почту» для рассылки прайса.

An "email account" is one SMTP mailbox the app sends the built price list FROM.
Cells with target='email' reference an account and carry their own recipients.
The SMTP password is DPAPI-encrypted by the DAL, masked in the field, and never
loaded back into the UI — when editing, a placeholder shows a password is stored
and it is only replaced if the user types a new one (same rule as the ZZap key).

«Отправить тестовое письмо» проверяет вход в ящик реальной отправкой короткого
письма (off the UI thread via AsyncRunner) — почтовые серверы, в отличие от ZZap,
позволяют дёшево проверить учётные данные.
"""
from __future__ import annotations

from PySide2.QtCore import Qt
from PySide2.QtWidgets import (QAbstractItemView, QComboBox, QDialog,
                               QDialogButtonBox, QFormLayout, QHBoxLayout,
                               QHeaderView, QInputDialog, QLabel, QLineEdit,
                               QMessageBox, QPushButton, QSpinBox, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from engine.config import EmailConfig
from engine.email_client import parse_recipients, send_test_email

from ...db.models import EmailAccount
from .. import theme
from ..context import AppContext
from ..workers import AsyncRunner

_SAVED_HINT = "•••••••• (сохранён — оставьте пустым, чтобы не менять)"
_USER_ROLE = int(Qt.UserRole)

# Пресеты популярных провайдеров: (подпись, smtp_host, port, security).
# У всех троих для SMTP нужен «пароль приложения», не обычный пароль от ящика.
_PRESETS = (
    ("Mail.ru", "smtp.mail.ru", 465, "ssl"),
    ("Яндекс", "smtp.yandex.ru", 465, "ssl"),
    ("Gmail", "smtp.gmail.com", 465, "ssl"),
    ("Другой сервер (вручную)", "", 465, "ssl"),
)

_SECURITY_ITEMS = (
    ("SSL (обычно порт 465)", "ssl"),
    ("STARTTLS (обычно порт 587)", "starttls"),
    ("Без шифрования", "none"),
)

_PASSWORD_HINT = (
    "Для Mail.ru, Яндекс и Gmail нужен «пароль приложения» — он создаётся в "
    "настройках безопасности почты. Обычный пароль от ящика по SMTP не работает.")


class EmailAccountDialog(QDialog):
    def __init__(self, account: EmailAccount | None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Почтовый ящик (отправитель)")
        self._account = account
        form = QFormLayout(self)

        self.ed_name = QLineEdit(account.name if account else "")
        self.cmb_preset = QComboBox()
        for label, *_ in _PRESETS:
            self.cmb_preset.addItem(label)
        self.cmb_preset.setCurrentIndex(len(_PRESETS) - 1)   # «вручную» по умолчанию
        self.cmb_preset.currentIndexChanged.connect(self._apply_preset)

        self.ed_login = QLineEdit(account.login if account else "")
        self.ed_login.setPlaceholderText("адрес@почты.ру")
        self.ed_password = QLineEdit()
        self.ed_password.setEchoMode(QLineEdit.Password)
        if account and account.has_password:
            self.ed_password.setPlaceholderText(_SAVED_HINT)
        self.ed_host = QLineEdit(account.smtp_host if account else "")
        self.ed_host.setPlaceholderText("smtp.example.ru")
        self.sp_port = QSpinBox()
        self.sp_port.setRange(1, 65535)
        self.sp_port.setValue(account.smtp_port if account else 465)
        self.cmb_security = QComboBox()
        for label, value in _SECURITY_ITEMS:
            self.cmb_security.addItem(label, value)
        self._select_security(account.security if account else "ssl")
        self.ed_from = QLineEdit(account.from_addr if account else "")
        self.ed_from.setPlaceholderText("пусто = как логин")

        hint = QLabel(_PASSWORD_HINT)
        hint.setWordWrap(True)
        hint.setProperty("role", "hint")

        form.addRow("Название", self.ed_name)
        form.addRow("Провайдер", self.cmb_preset)
        form.addRow("Email (логин)", self.ed_login)
        form.addRow("Пароль", self.ed_password)
        form.addRow(hint)
        form.addRow("SMTP-сервер", self.ed_host)
        form.addRow("Порт", self.sp_port)
        form.addRow("Защита", self.cmb_security)
        form.addRow("Отправитель («От кого»)", self.ed_from)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setProperty("class", "primary")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _apply_preset(self, index: int) -> None:
        _label, host, port, security = _PRESETS[index]
        if not host:            # «вручную» — ничего не трогаем
            return
        self.ed_host.setText(host)
        self.sp_port.setValue(port)
        self._select_security(security)

    def _select_security(self, value: str) -> None:
        idx = self.cmb_security.findData(value)
        self.cmb_security.setCurrentIndex(idx if idx >= 0 else 0)

    def _accept(self) -> None:
        if not self.ed_name.text().strip():
            QMessageBox.warning(self, "Проверьте поля", "Укажите название ящика.")
            return
        login = self.ed_login.text().strip()
        if not login or "@" not in login:
            QMessageBox.warning(self, "Проверьте поля",
                                "Укажите адрес почты (логин), например name@mail.ru.")
            return
        if not self.ed_host.text().strip():
            QMessageBox.warning(self, "Проверьте поля", "Укажите SMTP-сервер.")
            return
        has_saved = bool(self._account and self._account.has_password)
        if not self.ed_password.text() and not has_saved:
            QMessageBox.warning(self, "Проверьте поля",
                                "Введите пароль (для Mail.ru/Яндекс/Gmail — "
                                "пароль приложения).")
            return
        self.accept()

    def result_account(self) -> EmailAccount:
        return EmailAccount(
            id=self._account.id if self._account else None,
            name=self.ed_name.text().strip(),
            smtp_host=self.ed_host.text().strip(),
            smtp_port=int(self.sp_port.value()),
            security=self.cmb_security.currentData() or "ssl",
            login=self.ed_login.text().strip(),
            from_addr=self.ed_from.text().strip(),
        )

    def new_password(self) -> str | None:
        """The typed password (None if left blank = keep the stored one on edit)."""
        return self.ed_password.text() or None


class EmailAccountsScreen(QWidget):
    def __init__(self, ctx: AppContext, runner: AsyncRunner,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.runner = runner
        self._build()
        self.reload()

    def _build(self) -> None:
        root = QVBoxLayout(self)

        intro = QLabel(
            "Ящики, С КОТОРЫХ приложение отправляет прайс. Кому отправлять — "
            "задаётся в каждой ячейке (вкладка «Ячейки», цель «На почту»).")
        intro.setWordWrap(True)
        intro.setProperty("role", "hint")
        root.addWidget(intro)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            ["Название", "Email (логин)", "SMTP-сервер", "Пароль"])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.doubleClicked.connect(lambda *_: self._edit())
        root.addWidget(self.table, 1)

        btns = QHBoxLayout()
        b_add = QPushButton("Добавить…")
        b_add.setProperty("class", "primary")
        b_add.clicked.connect(self._add)
        b_edit = QPushButton("Изменить…")
        b_edit.clicked.connect(self._edit)
        b_del = QPushButton("Удалить")
        b_del.clicked.connect(self._delete)
        self.btn_test = QPushButton("Отправить тестовое письмо…")
        self.btn_test.setToolTip(
            "Проверка входа в почту: короткое письмо на указанный вами адрес. "
            "Прайс не отправляется.")
        self.btn_test.clicked.connect(self._send_test)
        for b in (b_add, b_edit, b_del):
            btns.addWidget(b)
        btns.addStretch(1)
        btns.addWidget(self.btn_test)
        root.addLayout(btns)

        self.lbl_status = QLabel("")
        self.lbl_status.setWordWrap(True)
        root.addWidget(self.lbl_status)

    def reload(self) -> None:
        accounts = self.ctx.db.list_email_accounts()
        self.table.setRowCount(len(accounts))
        for row, acc in enumerate(accounts):
            values = [acc.name, acc.login, f"{acc.smtp_host}:{acc.smtp_port}",
                      "задан" if acc.has_password else "— не задан —"]
            for col, text in enumerate(values):
                item = QTableWidgetItem(text)
                if col == 0:
                    item.setData(_USER_ROLE, acc.id)
                self.table.setItem(row, col, item)

    def _selected_id(self) -> int | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        return item.data(_USER_ROLE) if item else None

    def _add(self) -> None:
        dlg = EmailAccountDialog(None, self)
        if dlg.exec_() == QDialog.Accepted:
            self.ctx.db.add_email_account(dlg.result_account(), dlg.new_password())
            self.reload()

    def _edit(self) -> None:
        acc_id = self._selected_id()
        if acc_id is None:
            return
        account = self.ctx.db.get_email_account(acc_id)
        if account is None:
            return
        dlg = EmailAccountDialog(account, self)
        if dlg.exec_() == QDialog.Accepted:
            password = dlg.new_password()
            self.ctx.db.update_email_account(dlg.result_account(), password=password,
                                             update_password=password is not None)
            self.reload()

    def _delete(self) -> None:
        acc_id = self._selected_id()
        if acc_id is None:
            return
        if QMessageBox.question(self, "Удалить ящик?",
                                "Удалить выбранный почтовый ящик?") \
                == QMessageBox.Yes:
            self.ctx.db.delete_email_account(acc_id)
            self.reload()

    # --- test send (off the UI thread) ------------------------------------
    def _send_test(self) -> None:
        acc_id = self._selected_id()
        if acc_id is None:
            QMessageBox.information(self, "Выберите ящик",
                                    "Сначала выберите (или добавьте) почтовый ящик.")
            return
        account = self.ctx.db.get_email_account(acc_id)
        if account is None:
            return
        password = self.ctx.db.get_email_account_password(acc_id)
        if not password:
            QMessageBox.warning(self, "Нет пароля",
                                "У выбранного ящика не сохранён пароль — откройте "
                                "«Изменить…» и введите его.")
            return
        to_text, ok = QInputDialog.getText(
            self, "Тестовое письмо", "Кому отправить тестовое письмо:",
            QLineEdit.Normal, account.login)
        if not ok:
            return
        recipients = parse_recipients(to_text)
        if not recipients or any("@" not in r for r in recipients):
            QMessageBox.warning(self, "Проверьте адрес",
                                "Укажите корректный адрес получателя.")
            return
        cfg = EmailConfig(
            smtp_host=account.smtp_host, smtp_port=account.smtp_port,
            security=account.security, login=account.login, password=password,
            from_addr=account.from_addr or account.login, to_addrs=recipients)

        self.btn_test.setEnabled(False)
        theme.set_status(self.lbl_status,
                         "Отправляю тестовое письмо (до минуты)…", "info")
        self.runner.submit(lambda: send_test_email(cfg),
                           self._on_test_ok, self._on_test_err)

    def _on_test_ok(self, result: dict) -> None:
        self.btn_test.setEnabled(True)
        sent_to = ", ".join(result.get("recipients") or [])
        theme.set_status(self.lbl_status,
                         f"Тестовое письмо отправлено: {sent_to}.", "ok")
        QMessageBox.information(
            self, "Письмо отправлено",
            f"Тестовое письмо отправлено на {sent_to}.\n"
            "Проверьте входящие (и папку «Спам»). Настройки почты работают.")

    def _on_test_err(self, msg: str) -> None:
        self.btn_test.setEnabled(True)
        theme.set_status(self.lbl_status, "Не удалось отправить: " + msg, "error")
        QMessageBox.warning(self, "Не удалось отправить тестовое письмо", msg)
