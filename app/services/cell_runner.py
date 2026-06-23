"""CellRunner — run one upload cell end-to-end (headless, testable).

Pipeline for a single cell (ROADMAP §3 / PROMPT B.1):

    build_price_query(warehouses, price type)
      -> ComPriceSource.fetch_rows()   (1C COM)
      -> clean_rows
      -> apply_exclusions              (the cell's exclusion_list)
      -> build_xlsx(include_header=False, the cell's columns)
      -> upload_price(api_key, code_templ)   (ZZap)
      -> record run_history + per-cell Delivery (journal / state / pending)

**Safety (staging):** a real POST happens only when the GLOBAL staging kill-switch
is OFF *and* the cell's `staging_mode` is OFF. Otherwise the file is built and the
run is recorded as STAGED — nothing is sent. New cells default to staging.

**Failure handling:** errors *before* the upload (config / query / fetch / build)
finish the run as ERROR. An upload that throws is recoverable: the built file is
staged to the cell's pending slot, the run is FAIL, and `retry_pending` re-sends it
later (RESEND_OK on success). Secrets never reach a log or a stored message — every
error string is flattened through `error_text`, which redacts `Pwd=`/`Usr=`.

The COM source and the HTTP uploader are injected (defaults: `ComPriceSource`,
`upload_price`) so the whole runner is unit-tested with no live 1C/ZZap.

Threading: construct one Database (and thus one CellRunner) per worker thread — see
the DAL module docstring (WAL + busy_timeout make the per-thread connections safe).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from engine.config import DEFAULT_ZZAP_API_URL, ComConfig, ZzapConfig
from engine.delivery import Delivery
from engine.query_builder import build_price_query
from engine.sources.base import PriceSource
from engine.sources.com import ComPriceSource
from engine.transform import apply_exclusions, build_xlsx, clean_rows, parse_exclusions
from engine.zzap_client import upload_price

from ..db.dal import Database
from ..db.models import Cell, RunHistory
from .connection import ConnectionManager, error_text

log = logging.getLogger(__name__)

# run_history.status values produced by the runner.
RUN_RUNNING = "RUNNING"
RUN_OK = "OK"
RUN_STAGED = "STAGED"
RUN_FAIL = "FAIL"          # upload failed; file staged to pending for retry
RUN_ERROR = "ERROR"        # config/query/fetch/build error; nothing to retry
RUN_RESEND_OK = "RESEND_OK"

# Global staging kill-switch setting key (off by default; per-cell staging still guards).
SETTING_GLOBAL_STAGING = "staging_mode"

SourceFactory = Callable[[ComConfig], PriceSource]
Uploader = Callable[..., dict]


@dataclass
class RunResult:
    cell_id: int
    status: str
    rows_built: int = 0
    rows_sent: int | None = None
    file_path: str | None = None
    message: str = ""
    run_id: int | None = None
    posted: bool = False       # True only when a real POST to ZZap happened


class CellRunner:
    def __init__(self, db: Database, work_dir: str | Path, *,
                 source_factory: SourceFactory = ComPriceSource,
                 uploader: Uploader = upload_price,
                 connection_manager: ConnectionManager | None = None) -> None:
        self.db = db
        self.work_dir = Path(work_dir)
        self._source_factory = source_factory
        self._uploader = uploader
        self._cm = connection_manager or ConnectionManager()

    # --- public ----------------------------------------------------------
    def run_cell(self, cell: Cell | int) -> RunResult:
        """Run a single cell end-to-end. Never raises — every outcome is a RunResult
        and a run_history row."""
        cell = self._resolve(cell)
        run_id = self.db.add_run(
            RunHistory(cell_id=cell.id, started_at=self._now(), status=RUN_RUNNING))
        delivery = Delivery(self._cell_dir(cell.id))

        try:
            file_path, rows_built = self._build(cell)
        except Exception as e:  # noqa: BLE001 - map any pre-upload failure to ERROR
            msg = error_text(e)
            log.warning("Cell %s build failed: %s", cell.id, msg)
            delivery.journal(RUN_ERROR, msg)
            self.db.finish_run(run_id, self._now(), RUN_ERROR, message=msg)
            return RunResult(cell.id, RUN_ERROR, run_id=run_id, message=msg)

        if self._global_staging() or cell.staging_mode:
            delivery.journal(RUN_STAGED, f"rows={rows_built} (staging — не отправлено)")
            self.db.finish_run(run_id, self._now(), RUN_STAGED,
                               rows_note=str(rows_built),
                               message="Staging — файл собран, не отправлен.")
            return RunResult(cell.id, RUN_STAGED, rows_built=rows_built,
                             file_path=str(file_path), run_id=run_id,
                             message="Staging — файл собран, не отправлен.")

        return self._upload(cell, delivery, run_id, file_path, rows_built)

    def run_all_enabled(self) -> list[RunResult]:
        """Run every enabled cell, returning one RunResult each (in cell id order)."""
        return [self.run_cell(c) for c in self.db.list_cells(enabled_only=True)]

    def retry_pending(self, cell: Cell | int) -> RunResult | None:
        """Re-send a cell's previously staged (pending) file. Returns None if there
        is nothing pending."""
        cell = self._resolve(cell)
        delivery = Delivery(self._cell_dir(cell.id))
        pending = delivery.get_pending()
        if not pending:
            return None

        cabinet = self.db.get_cabinet(cell.cabinet_id) if cell.cabinet_id is not None else None
        if cabinet is None:
            return RunResult(cell.id, RUN_ERROR,
                             message="Нет кабинета ZZap для досылки.")
        zcfg = self._zzap_config(cell, cabinet)
        if zcfg is None:
            return RunResult(cell.id, RUN_ERROR,
                             message="Нет API-ключа/кода шаблона для досылки.")

        file_path = Path(pending["file"])
        file_name = pending.get("file_name", file_path.name)
        rows = pending.get("rows", "?")          # carried over from the original FAIL
        rows_sent = rows if isinstance(rows, int) else None
        run_id = self.db.add_run(
            RunHistory(cell_id=cell.id, started_at=self._now(), status=RUN_RUNNING))
        try:
            data = self._uploader(zcfg, file_path, file_name)
        except Exception as e:  # noqa: BLE001
            reason = error_text(e)
            delivery.journal(RUN_FAIL, f"повторная отправка не удалась: {reason}")
            self.db.finish_run(run_id, self._now(), RUN_FAIL,
                               message=f"Досылка не удалась: {reason}")
            return RunResult(cell.id, RUN_FAIL, file_path=str(file_path),
                             run_id=run_id, message=reason)

        delivery.record_success(file_name, rows, _file_url(data), tag=RUN_RESEND_OK)
        self.db.finish_run(run_id, self._now(), RUN_RESEND_OK, rows_sent=rows_sent,
                           rows_note=str(rows), message="Досылка выполнена.")
        return RunResult(cell.id, RUN_RESEND_OK, rows_sent=rows_sent,
                         file_path=str(file_path), run_id=run_id, posted=True,
                         message="Досылка выполнена.")

    # --- build (everything up to and including the XLSX) ------------------
    def _build(self, cell: Cell) -> tuple[Path, int]:
        if cell.connection_id is None:
            raise ValueError("У ячейки не задано подключение 1С.")
        conn = self.db.get_connection(cell.connection_id)
        if conn is None:
            raise ValueError("Подключение 1С ячейки не найдено.")
        if cell.cabinet_id is None or self.db.get_cabinet(cell.cabinet_id) is None:
            raise ValueError("У ячейки не выбран кабинет ZZap.")

        query = build_price_query(cell.warehouses, cell.price_type)
        password = self.db.get_connection_password(conn.id)
        comcfg = self._cm.to_com_config(conn, password, query)

        rows = clean_rows(self._source_factory(comcfg).fetch_rows())
        rows = apply_exclusions(rows, self._exclusions(cell))
        file_path = build_xlsx(rows, self._cell_dir(cell.id) / "price.xlsx",
                               include_header=cell.include_header, columns=cell.columns)
        return file_path, len(rows)

    def _upload(self, cell: Cell, delivery: Delivery, run_id: int,
                file_path: Path, rows_built: int) -> RunResult:
        cabinet = self.db.get_cabinet(cell.cabinet_id)
        zcfg = self._zzap_config(cell, cabinet)
        if zcfg is None:
            # Can't send (no key / no template) — not transient, so ERROR not FAIL.
            # code_templ is checkable without decrypting, so disambiguate off it.
            msg = ("У ячейки не задан код шаблона ZZap (code_templ)." if not cell.code_templ
                   else "У кабинета ZZap не задан API-ключ.")
            delivery.journal(RUN_ERROR, msg)
            self.db.finish_run(run_id, self._now(), RUN_ERROR,
                               rows_note=str(rows_built), message=msg)
            return RunResult(cell.id, RUN_ERROR, rows_built=rows_built,
                             file_path=str(file_path), run_id=run_id, message=msg)

        try:
            data = self._uploader(zcfg, file_path, file_path.name)
        except Exception as e:  # noqa: BLE001 - transient: stage for retry
            reason = error_text(e)
            log.warning("Cell %s upload failed: %s", cell.id, reason)
            if delivery.mark_pending(file_path, file_path.name, reason, rows=rows_built):
                self.db.finish_run(run_id, self._now(), RUN_FAIL,
                                   rows_note=str(rows_built),
                                   message=f"Ошибка отправки, отложено для досылки: {reason}")
                return RunResult(cell.id, RUN_FAIL, rows_built=rows_built,
                                 file_path=str(file_path), run_id=run_id, message=reason)
            # Staging the file itself failed -> nothing to retry, surface as ERROR.
            self.db.finish_run(run_id, self._now(), RUN_ERROR,
                               rows_note=str(rows_built),
                               message=f"Ошибка отправки, файл не удалось отложить: {reason}")
            return RunResult(cell.id, RUN_ERROR, rows_built=rows_built,
                             file_path=str(file_path), run_id=run_id, message=reason)

        delivery.record_success(file_path.name, rows_built, _file_url(data))
        self.db.finish_run(run_id, self._now(), RUN_OK, rows_sent=rows_built,
                           rows_note=str(rows_built), message="Загружено в ZZap.")
        return RunResult(cell.id, RUN_OK, rows_built=rows_built, rows_sent=rows_built,
                         file_path=str(file_path), run_id=run_id, posted=True,
                         message="Загружено в ZZap.")

    # --- helpers ---------------------------------------------------------
    def _zzap_config(self, cell: Cell, cabinet) -> ZzapConfig | None:
        api_key = self.db.get_cabinet_api_key(cabinet.id)
        if not api_key or not cell.code_templ:
            return None
        return ZzapConfig(api_key=api_key, code_templ=cell.code_templ,
                          api_url=cabinet.api_url or DEFAULT_ZZAP_API_URL,
                          test_mode=False)

    def _exclusions(self, cell: Cell) -> set[str]:
        if not cell.exclusion_list_id:
            return set()
        lst = self.db.get_exclusion_list(cell.exclusion_list_id)
        return parse_exclusions(lst.articles) if lst else set()

    def _resolve(self, cell: Cell | int) -> Cell:
        if isinstance(cell, Cell):
            return cell
        resolved = self.db.get_cell(cell)
        if resolved is None:
            raise ValueError(f"Ячейка id={cell} не найдена.")
        return resolved

    def _global_staging(self) -> bool:
        return self.db.get_bool(SETTING_GLOBAL_STAGING, default=False)

    def _cell_dir(self, cell_id: int) -> Path:
        return self.work_dir / f"cell_{cell_id}"

    @staticmethod
    def _now() -> str:
        return datetime.now().isoformat(timespec="seconds")


def _file_url(data) -> str | None:
    if isinstance(data, dict):
        result = data.get("result")
        if isinstance(result, dict):
            return result.get("file_url")
    return None
