"""Предпросмотр выгрузки — «собрать файл без отправки».

Зачем отдельный модуль: сборка обязана идти в ТОМ ЖЕ коротком дочернем процессе, что и
боевая выгрузка (иначе GUI загрузит среду 1С COM ~400 МБ и потечёт хэндлами — см.
scheduler). Но, в отличие от выгрузки, предпросмотр НИЧЕГО не отправляет в ZZap и не
пишет ни run_history, ни журнал доставки, ни pending-файл.

Обмен результатом с родителем — через маленький JSON рядом с собранным preview.xlsx
(`cell_<id>/preview.json`), как это уже делает Delivery со своим состоянием.
"""
from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Callable

from .cell_runner import PreviewResult, preview_json_path
from .scheduler import child_base_command, run_child_process

log = logging.getLogger(__name__)

# Предпросмотр читает тот же объём данных, что и выгрузка, поэтому лимит тот же порядок.
DEFAULT_PREVIEW_TIMEOUT_SECONDS = 1800.0

Launcher = Callable[[list[str], float], None]


def build_preview_command(cell_id: int) -> list[str]:
    """Команда «собрать файл ячейки без отправки» в дочернем процессе."""
    return child_base_command() + ["--preview-cell", str(cell_id)]


def run_preview(cell_id: int, work_dir: str | Path, *,
                launcher: Launcher = run_child_process,
                timeout: float = DEFAULT_PREVIEW_TIMEOUT_SECONDS) -> PreviewResult:
    """Собрать файл ячейки в дочернем процессе и вернуть результат. Никогда не бросает.

    Старый preview.json удаляется ДО запуска: иначе упавший (или убитый по таймауту)
    дочерний процесс оставил бы прошлый результат, и мы бы выдали его за свежий.
    """
    path = preview_json_path(work_dir, cell_id)
    try:
        path.unlink(missing_ok=True)
    except OSError as e:
        log.warning("Не удалось удалить старый preview.json: %s", e)

    try:
        launcher(build_preview_command(cell_id), timeout)
    except subprocess.TimeoutExpired:
        return PreviewResult(cell_id=cell_id, ok=False,
                             message=("Проверка превысила лимит времени и была "
                                      "остановлена. Ничего не отправлено."))
    except Exception as e:  # noqa: BLE001 - не дать проверке уронить GUI
        return PreviewResult(cell_id=cell_id, ok=False,
                             message=f"Не удалось запустить проверку: {e}")

    if not path.exists():
        return PreviewResult(cell_id=cell_id, ok=False,
                             message=("Проверка не вернула результат (дочерний процесс "
                                      "завершился аварийно). Подробности — в журнале."))
    try:
        return PreviewResult.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError) as e:
        return PreviewResult(cell_id=cell_id, ok=False,
                             message=f"Повреждён результат проверки: {e}")
