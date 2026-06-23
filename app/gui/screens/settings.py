"""Settings screen — upload interval, the GLOBAL staging kill-switch, autostart.

These are key/value rows in the ``setting`` table. The global staging switch is the
``staging_mode`` key read by CellRunner (when ON, nothing is ever sent, regardless
of per-cell settings). The interval and autostart are consumed by the Phase 4
scheduler/tray; here they are just persisted.
"""
from __future__ import annotations

from PySide6.QtWidgets import (QCheckBox, QFormLayout, QGroupBox, QHBoxLayout,
                               QLabel, QPushButton, QSpinBox, QVBoxLayout, QWidget)

from ...services.cell_runner import SETTING_GLOBAL_STAGING
from .. import theme
from ..context import AppContext

SETTING_INTERVAL_HOURS = "interval_hours"
SETTING_AUTOSTART = "autostart"
_DEFAULT_INTERVAL = 5


class SettingsScreen(QWidget):
    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._build()
        self.reload()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        box = QGroupBox("Параметры")
        form = QFormLayout(box)

        self.sp_interval = QSpinBox()
        self.sp_interval.setRange(1, 168)
        self.sp_interval.setSuffix(" ч")
        form.addRow("Интервал авто-выгрузки", self.sp_interval)

        self.cb_staging = QCheckBox(
            "Глобальный режим staging — НИЧЕГО не отправлять в ZZap (общий стоп-кран)")
        form.addRow("", self.cb_staging)

        self.cb_autostart = QCheckBox("Запускать вместе с Windows")
        form.addRow("", self.cb_autostart)
        root.addWidget(box)

        note = QLabel("Интервал и автозапуск применяются фоновым планировщиком "
                      "(добавляется в Phase 4). Глобальный staging действует сразу.")
        note.setWordWrap(True)
        note.setProperty("role", "hint")
        root.addWidget(note)

        btns = QHBoxLayout()
        b_save = QPushButton("Сохранить")
        b_save.setProperty("class", "primary")
        b_save.clicked.connect(self._save)
        btns.addWidget(b_save)
        btns.addStretch(1)
        root.addLayout(btns)

        self.lbl_status = QLabel("")
        root.addWidget(self.lbl_status)
        root.addStretch(1)

    def reload(self) -> None:
        self.sp_interval.setValue(
            self.ctx.db.get_int(SETTING_INTERVAL_HOURS, _DEFAULT_INTERVAL))
        self.cb_staging.setChecked(
            self.ctx.db.get_bool(SETTING_GLOBAL_STAGING, default=False))
        self.cb_autostart.setChecked(
            self.ctx.db.get_bool(SETTING_AUTOSTART, default=False))

    def _save(self) -> None:
        self.ctx.db.set_setting(SETTING_INTERVAL_HOURS, str(self.sp_interval.value()))
        self.ctx.db.set_bool(SETTING_GLOBAL_STAGING, self.cb_staging.isChecked())
        self.ctx.db.set_bool(SETTING_AUTOSTART, self.cb_autostart.isChecked())
        theme.set_status(self.lbl_status, "Сохранено.", "ok")
