"""Настройка логирования приложения (Phase 5): консоль + ротация файла.

Когда приложение работает свёрнутым в трей, консоли не видно — поэтому пишем лог в
файл с ротацией под каталогом данных пользователя (`%LOCALAPPDATA%\\ZZapSync\\logs\\
app.log`). UTF-8 обязателен, иначе кириллица в логах ломается (PROJECT_MEMORY §1).

`setup_logging` идемпотентна: повторный вызов не добавляет дубликаты обработчиков
(важно для тестов и для повторной инициализации).
"""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from . import paths

_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_MAX_BYTES = 1_000_000
_BACKUP_COUNT = 5
# Маркеры, чтобы распознать «свои» обработчики при повторном вызове.
_CONSOLE_TAG = "zzap_console"
_FILE_TAG = "zzap_rotating_file"


def setup_logging(level: int = logging.INFO) -> RotatingFileHandler:
    """Подключает консольный вывод и ротируемый файловый лог к корневому логгеру.

    Возвращает файловый обработчик (удобно для тестов). Безопасно вызывать повторно.
    """
    paths.ensure_dirs()
    root = logging.getLogger()
    root.setLevel(level)

    existing = {getattr(h, "_zzap_tag", None): h for h in root.handlers}

    if _CONSOLE_TAG not in existing:
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter(_FORMAT))
        console._zzap_tag = _CONSOLE_TAG  # type: ignore[attr-defined]
        root.addHandler(console)

    file_handler = existing.get(_FILE_TAG)
    if file_handler is None:
        log_path = paths.logs_dir() / "app.log"
        file_handler = RotatingFileHandler(
            log_path, maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT, encoding="utf-8")
        file_handler.setFormatter(logging.Formatter(_FORMAT))
        file_handler._zzap_tag = _FILE_TAG  # type: ignore[attr-defined]
        root.addHandler(file_handler)

    return file_handler
