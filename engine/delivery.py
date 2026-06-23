"""Журнал загрузок и механизм «дослать отложенное» (per-target).

Если в момент выгрузки нет связи/интернета (или ПК был выключен), прайс не теряется:
  - каждая попытка пишется в журнал (человекочитаемый upload_journal.log);
  - при неудаче отправки готовый файл откладывается в pending/ + отметка в state.json;
  - отдельный проход (flush) дошлёт отложенный прайс, как только связь вернётся.

В приложении у КАЖДОЙ ячейки (cell) свой каталог состояния, поэтому пути
инжектируются (раньше в CLI они были модульными глобалами). Один экземпляр
Delivery = одна ячейка.
"""
from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)


class Delivery:
    def __init__(self, base_dir: str | Path,
                 journal_name: str = "upload_journal.log",
                 state_name: str = "state.json",
                 pending_dirname: str = "pending",
                 pending_filename: str = "price_pending.xlsx") -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.journal_path = self.base_dir / journal_name
        self.state_path = self.base_dir / state_name
        self.pending_dir = self.base_dir / pending_dirname
        self.pending_file = self.pending_dir / pending_filename

    # --- журнал -----------------------------------------------------------
    def journal(self, status: str, message: str) -> None:
        line = f"{datetime.now():%Y-%m-%d %H:%M:%S}\t{status}\t{message}\n"
        try:
            with open(self.journal_path, "a", encoding="utf-8") as f:
                f.write(line)
        except OSError as e:  # журнал не должен ломать процесс
            log.warning("Не удалось записать журнал: %s", e)

    # --- состояние --------------------------------------------------------
    def _load_state(self) -> dict:
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save_state(self, state: dict) -> None:
        try:
            self.state_path.write_text(
                json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as e:
            log.warning("Не удалось сохранить state.json: %s", e)

    def read_state(self) -> dict:
        """Публичное чтение состояния (last_success / last_rows / pending)."""
        return self._load_state()

    # --- успех / отложенное ----------------------------------------------
    def record_success(self, file_name: str, rows: int | str, file_url: str | None,
                        tag: str = "OK") -> None:
        state = self._load_state()
        state["last_success"] = datetime.now().isoformat(timespec="seconds")
        state["last_rows"] = rows
        state.pop("pending", None)
        self._save_state(state)
        self.clear_pending()
        self.journal(tag, f"rows={rows} file={file_name} url={file_url or '-'}")

    def mark_pending(self, built_path: str | Path, file_name: str, reason: str) -> None:
        """Откладывает готовый файл для повторной отправки."""
        self.pending_dir.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copyfile(built_path, self.pending_file)
        except OSError as e:
            log.error("Не удалось отложить файл для досылки: %s", e)
            self.journal("FAIL",
                         f"upload failed AND cannot stage pending: {reason}; copy error: {e}")
            return
        state = self._load_state()
        state["pending"] = {
            "file": str(self.pending_file),
            "file_name": file_name,
            "since": datetime.now().isoformat(timespec="seconds"),
        }
        self._save_state(state)
        self.journal("FAIL", f"upload failed, отложено для досылки: {reason}")

    def get_pending(self) -> dict | None:
        pending = self._load_state().get("pending")
        if pending and Path(pending.get("file", "")).exists():
            return pending
        return None

    def clear_pending(self) -> None:
        state = self._load_state()
        state.pop("pending", None)
        self._save_state(state)
        try:
            self.pending_file.unlink(missing_ok=True)
        except OSError:
            pass
