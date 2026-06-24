"""Settings screen — upload interval, the GLOBAL staging kill-switch, autostart.

These are key/value rows in the ``setting`` table. The global staging switch is the
``staging_mode`` key read by CellRunner (when ON, nothing is ever sent, regardless of
per-cell settings). Saving the interval **reschedules the running scheduler live** (no
restart); toggling autostart adds/removes the per-user ``HKCU\\...\\Run`` entry. The
scheduler service + autostart manager are injected by the main window (both default to
None so the screen still builds standalone in tests).
"""
from __future__ import annotations

from PySide6.QtWidgets import (QCheckBox, QFormLayout, QGroupBox, QHBoxLayout,
                               QLabel, QPushButton, QSpinBox, QVBoxLayout, QWidget)

from ...services.autostart import (SETTING_AUTOSTART, AutostartManager,
                                   launch_command)
from ...services.cell_runner import SETTING_GLOBAL_STAGING
from ...services.scheduler import (DEFAULT_INTERVAL_HOURS, SETTING_INTERVAL_HOURS,
                                   SchedulerService)
from .. import theme
from ..context import AppContext


class SettingsScreen(QWidget):
    def __init__(self, ctx: AppContext,
                 scheduler_service: SchedulerService | None = None,
                 autostart_manager: AutostartManager | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._service = scheduler_service
        self._autostart = autostart_manager
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

        note = QLabel("Интервал применяется сразу (планировщик перепланируется без "
                      "перезапуска). Автозапуск — отдельная запись в реестре пользователя. "
                      "Глобальный staging действует немедленно.")
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
            self.ctx.db.get_int(SETTING_INTERVAL_HOURS, DEFAULT_INTERVAL_HOURS))
        self.cb_staging.setChecked(
            self.ctx.db.get_bool(SETTING_GLOBAL_STAGING, default=False))
        self.cb_autostart.setChecked(
            self.ctx.db.get_bool(SETTING_AUTOSTART, default=False))

    def _save(self) -> None:
        interval = self.sp_interval.value()
        self.ctx.db.set_setting(SETTING_INTERVAL_HOURS, str(interval))
        self.ctx.db.set_bool(SETTING_GLOBAL_STAGING, self.cb_staging.isChecked())
        self.ctx.db.set_bool(SETTING_AUTOSTART, self.cb_autostart.isChecked())

        msgs = ["Сохранено."]
        if self._service is not None:
            self._service.reschedule(interval)
            msgs.append("Планировщик перепланирован.")
        if self._autostart is not None:
            try:
                self._autostart.apply(self.cb_autostart.isChecked(), launch_command())
            except OSError as e:  # registry write failed — surface, don't crash
                theme.set_status(self.lbl_status,
                                 f"Сохранено, но автозапуск не изменён: {e}", "error")
                return
        theme.set_status(self.lbl_status, " ".join(msgs), "ok")
