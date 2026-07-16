"""CellRunner — run one upload cell end-to-end (headless, testable).

Pipeline for a single cell (ROADMAP §3 / PROMPT B.1):

    build_price_query(warehouses, price type)
      -> ComPriceSource.fetch_rows()   (1C COM; source='com')
         — or —  OdataPriceSource.fetch_rows()  (1C OData/HTTP; source='odata')
      -> clean_rows
      -> apply_exclusions              (the cell's exclusion_list)
      -> build_xlsx(include_header=False, the cell's columns)
      -> upload_price(api_key, code_templ)   (ZZap; cell.target == 'zzap')
         — or — send_price_email(smtp account, recipients)  (cell.target == 'email')
      -> record run_history + per-cell Delivery (journal / state / pending)

**Safety (0-row guard):** a real upload FULLY REPLACES the ZZap template, so a build
that yields 0 rows is refused as ERROR (never wipe a template with an empty file).

**Failure handling:** errors *before* the upload (config / query / fetch / build)
finish the run as ERROR. A permanent ZZap rejection (bad key/url/content,
`ZzapPermanentError`) is ERROR — retrying can't help. A transient failure (5xx / 429 /
timeout / no network) stages the built file to the cell's pending slot as FAIL, and
`retry_pending` re-sends it later (RESEND_OK on success). Secrets never reach a log or a
stored message — every error string is flattened through `error_text` (redacts `Pwd=`/`Usr=`).

The COM/OData sources and the HTTP uploader are injected (defaults: `ComPriceSource`,
`OdataPriceSource`, `upload_price`); the source is chosen per cell by the connection's
`source`, so the whole runner is unit-tested with no live 1C/ZZap.

Threading: construct one Database (and thus one CellRunner) per worker thread — see
the DAL module docstring (WAL + busy_timeout make the per-thread connections safe).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from engine.config import (DEFAULT_ZZAP_API_URL, ComConfig, EmailConfig,
                           OdataConfig, ZzapConfig)
from engine.delivery import Delivery
from engine.email_client import (EmailPermanentError, parse_recipients,
                                 send_price_email)
from engine.query_builder import build_price_query
from engine.sources.base import PriceSource
from engine.sources.com import ComPriceSource
from engine.sources.odata import OdataPriceSource
from engine.transform import apply_exclusions, build_xlsx, clean_rows, parse_exclusions
from engine.zzap_client import ZzapPermanentError, upload_price

from ..db.dal import Database
from ..db.models import Cell, RunHistory
from .connection import ConnectionManager, error_text

log = logging.getLogger(__name__)

# run_history.status values produced by the runner.
RUN_RUNNING = "RUNNING"
RUN_OK = "OK"
RUN_FAIL = "FAIL"          # upload failed; file staged to pending for retry
RUN_ERROR = "ERROR"        # config/query/fetch/build error; nothing to retry
RUN_RESEND_OK = "RESEND_OK"

SourceFactory = Callable[[ComConfig], PriceSource]
OdataSourceFactory = Callable[[OdataConfig], PriceSource]
Uploader = Callable[..., dict]
EmailSender = Callable[..., dict]

# Cell.target values (delivery channel).
TARGET_ZZAP = "zzap"
TARGET_EMAIL = "email"

# Dry-run («Собрать файл без отправки»): the built file and the handoff JSON the child
# process leaves for the GUI. Kept apart from the real price.xlsx so a preview can never
# be confused with — or clobber — a build that is queued for delivery.
PREVIEW_XLSX = "preview.xlsx"
PREVIEW_JSON = "preview.json"


def preview_json_path(work_dir: str | Path, cell_id: int) -> Path:
    return Path(work_dir) / f"cell_{cell_id}" / PREVIEW_JSON


@dataclass
class PreviewResult:
    """Outcome of a dry run: the file was built, NOTHING was sent to ZZap.

    `zero_quantity`/`zero_price` are the safety signal: a wrong OData field name yields
    rows that all carry 0 — `clean_rows` keeps them (it only drops rows without an
    article), and the 0-row guard would NOT catch that before an upload wiped the
    template. The GUI surfaces these counts prominently.
    """
    cell_id: int
    ok: bool = False
    rows: int = 0
    zero_quantity: int = 0
    zero_price: int = 0
    file_path: str | None = None
    sample: list[list] = field(default_factory=list)   # first rows, for eyeballing
    message: str = ""

    def to_dict(self) -> dict:
        return {"cell_id": self.cell_id, "ok": self.ok, "rows": self.rows,
                "zero_quantity": self.zero_quantity, "zero_price": self.zero_price,
                "file_path": self.file_path, "sample": self.sample,
                "message": self.message}

    @classmethod
    def from_dict(cls, data: dict) -> "PreviewResult":
        return cls(cell_id=int(data.get("cell_id", 0)), ok=bool(data.get("ok")),
                   rows=int(data.get("rows", 0)),
                   zero_quantity=int(data.get("zero_quantity", 0)),
                   zero_price=int(data.get("zero_price", 0)),
                   file_path=data.get("file_path"), sample=list(data.get("sample") or []),
                   message=str(data.get("message") or ""))

    def write(self, work_dir: str | Path) -> Path:
        path = preview_json_path(work_dir, self.cell_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False), encoding="utf-8")
        return path


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
                 odata_source_factory: OdataSourceFactory = OdataPriceSource,
                 uploader: Uploader = upload_price,
                 email_sender: EmailSender = send_price_email,
                 connection_manager: ConnectionManager | None = None,
                 network_check: Callable[[], bool] | None = None) -> None:
        self.db = db
        self.work_dir = Path(work_dir)
        self._source_factory = source_factory
        self._odata_source_factory = odata_source_factory
        self._uploader = uploader
        self._email_sender = email_sender
        self._cm = connection_manager or ConnectionManager()
        # Optional pre-POST reachability probe (Phase 4 offline recovery). When it
        # returns False the file is staged to pending WITHOUT an HTTP attempt — the
        # flush pass re-sends it once the network returns. Default None = no probe
        # (behaviour identical to Phase 2).
        self._network_check = network_check

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

        # Safety: a real ZZap upload FULLY REPLACES the template, so a 0-row file would
        # silently wipe it. Refuse (ERROR) instead of publishing an empty price list.
        # For e-mail cells an empty file is not destructive, but 0 rows almost always
        # means a misconfiguration — refuse too, so the journal flags it loudly.
        if rows_built == 0:
            msg = ("1С вернула 0 строк — отправка отменена (пустой прайс не "
                   "отправляем). Проверьте фильтры/подключение ячейки."
                   if cell.target == TARGET_EMAIL else
                   "1С вернула 0 строк — боевая выгрузка отменена, чтобы не очистить "
                   "шаблон ZZap. Проверьте склады/вид цены/фильтры ячейки.")
            log.warning("Cell %s: 0 строк — боевая выгрузка отменена.", cell.id)
            delivery.journal(RUN_ERROR, msg)
            self.db.finish_run(run_id, self._now(), RUN_ERROR, rows_note="0", message=msg)
            return RunResult(cell.id, RUN_ERROR, rows_built=0,
                             file_path=str(file_path), run_id=run_id, message=msg)

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

        file_path = Path(pending["file"])
        file_name = pending.get("file_name", file_path.name)

        if cell.target == TARGET_EMAIL:
            ecfg, why = self._email_config(cell)
            if ecfg is None:
                return RunResult(cell.id, RUN_ERROR,
                                 message=f"Досылка невозможна: {why}")
            send = lambda: self._email_sender(ecfg, file_path, file_name)  # noqa: E731
        else:
            cabinet = (self.db.get_cabinet(cell.cabinet_id)
                       if cell.cabinet_id is not None else None)
            if cabinet is None:
                return RunResult(cell.id, RUN_ERROR,
                                 message="Нет кабинета ZZap для досылки.")
            zcfg = self._zzap_config(cell, cabinet)
            if zcfg is None:
                return RunResult(cell.id, RUN_ERROR,
                                 message="Нет API-ключа/кода шаблона для досылки.")
            send = lambda: self._uploader(zcfg, file_path, file_name)  # noqa: E731

        rows = pending.get("rows", "?")          # carried over from the original FAIL
        rows_sent = rows if isinstance(rows, int) else None
        run_id = self.db.add_run(
            RunHistory(cell_id=cell.id, started_at=self._now(), status=RUN_RUNNING))
        try:
            data = send()
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

    # --- dry run (build the file, send NOTHING) ---------------------------
    def build_preview(self, cell: Cell | int) -> PreviewResult:
        """Build the cell's XLSX and stop — no upload, no run_history, no pending file.

        The safe way to validate a connection (especially OData, where a wrong field name
        silently yields zeros). Never raises: every failure comes back as ok=False.
        """
        # Resolve inside the guard too: an unknown id must come back as ok=False, not as
        # an exception — the child process has to leave a readable result either way.
        cell_id = cell.id if isinstance(cell, Cell) else int(cell)
        try:
            cell = self._resolve(cell)
            cell_id = cell.id
            conn = self._connection_for(cell)
            rows = self._rows_for(cell, conn)
            file_path = build_xlsx(rows, self._cell_dir(cell.id) / PREVIEW_XLSX,
                                   include_header=cell.include_header,
                                   columns=cell.columns)
        except Exception as e:  # noqa: BLE001 - a preview must never crash the caller
            msg = error_text(e)
            log.warning("Cell %s preview failed: %s", cell_id, msg)
            return PreviewResult(cell_id=cell_id or 0, ok=False, message=msg)

        return PreviewResult(
            cell_id=cell.id, ok=True, rows=len(rows),
            zero_quantity=sum(1 for r in rows if not r.quantity),
            zero_price=sum(1 for r in rows if not r.price),
            file_path=str(file_path),
            sample=[[r.producer, r.number, r.name, r.quantity, r.price] for r in rows[:10]],
            message="Файл собран. В ZZap ничего не отправлено.")

    # --- build (everything up to and including the XLSX) ------------------
    def _connection_for(self, cell: Cell):
        if cell.connection_id is None:
            raise ValueError("У ячейки не задано подключение 1С.")
        conn = self.db.get_connection(cell.connection_id)
        if conn is None:
            raise ValueError("Подключение 1С ячейки не найдено.")
        return conn

    def _rows_for(self, cell: Cell, conn) -> list:
        """Fetch + clean + apply exclusions. Shared by the real run and the preview."""
        password = self.db.get_connection_password(conn.id)
        if conn.source == "odata":
            # OData reads via the connection's own queries (base_url + entity sets);
            # the cell's warehouses/price type are a COM-query concept and unused here.
            source = self._odata_source_factory(self._cm.to_odata_config(conn, password))
        else:
            query = build_price_query(cell.warehouses, cell.price_type)
            source = self._source_factory(self._cm.to_com_config(conn, password, query))
        rows = clean_rows(source.fetch_rows())
        return apply_exclusions(rows, self._exclusions(cell))

    def _build(self, cell: Cell) -> tuple[Path, int]:
        conn = self._connection_for(cell)
        if cell.target == TARGET_EMAIL:
            if (cell.email_account_id is None
                    or self.db.get_email_account(cell.email_account_id) is None):
                raise ValueError("У ячейки не выбран почтовый ящик для отправки.")
            if not parse_recipients(cell.email_to):
                raise ValueError("У ячейки не указан адрес получателя прайса.")
        elif cell.cabinet_id is None or self.db.get_cabinet(cell.cabinet_id) is None:
            raise ValueError("У ячейки не выбран кабинет ZZap.")

        rows = self._rows_for(cell, conn)
        file_path = build_xlsx(rows, self._cell_dir(cell.id) / "price.xlsx",
                               include_header=cell.include_header, columns=cell.columns)
        return file_path, len(rows)

    def _upload(self, cell: Cell, delivery: Delivery, run_id: int,
                file_path: Path, rows_built: int) -> RunResult:
        # Resolve the delivery channel (ZZap upload vs e-mail attachment). A missing
        # config (no key / no template / no mailbox) is ERROR, not FAIL — retrying
        # can't conjure a secret.
        if cell.target == TARGET_EMAIL:
            ecfg, why = self._email_config(cell)
            if ecfg is None:
                delivery.journal(RUN_ERROR, why)
                self.db.finish_run(run_id, self._now(), RUN_ERROR,
                                   rows_note=str(rows_built), message=why)
                return RunResult(cell.id, RUN_ERROR, rows_built=rows_built,
                                 file_path=str(file_path), run_id=run_id, message=why)
            send = lambda: self._email_sender(ecfg, file_path, file_path.name)  # noqa: E731
            success_msg = "Отправлено на почту: " + ", ".join(ecfg.to_addrs) + "."
        else:
            cabinet = self.db.get_cabinet(cell.cabinet_id)
            zcfg = self._zzap_config(cell, cabinet)
            if zcfg is None:
                # code_templ is checkable without decrypting, so disambiguate off it.
                msg = ("У ячейки не задан код шаблона ZZap (code_templ)."
                       if not cell.code_templ else "У кабинета ZZap не задан API-ключ.")
                delivery.journal(RUN_ERROR, msg)
                self.db.finish_run(run_id, self._now(), RUN_ERROR,
                                   rows_note=str(rows_built), message=msg)
                return RunResult(cell.id, RUN_ERROR, rows_built=rows_built,
                                 file_path=str(file_path), run_id=run_id, message=msg)
            send = lambda: self._uploader(zcfg, file_path, file_path.name)  # noqa: E731
            success_msg = "Загружено в ZZap."

        # Phase 4: skip the POST entirely when the network is known-down — stage the
        # built file for the flush pass instead of provoking a guaranteed FAIL.
        if self._network_check is not None and not self._network_check():
            log.info("Cell %s: сеть недоступна, откладываю для досылки.", cell.id)
            return self._stage_pending(cell, delivery, run_id, file_path, rows_built,
                                       "Нет подключения к сети.")

        try:
            data = send()
        except (ZzapPermanentError, EmailPermanentError) as e:
            # Permanent (bad key/password / wrong url / rejected content or address):
            # retrying won't help — surface the actionable RU message as ERROR,
            # don't keep a pending file.
            reason = error_text(e)
            log.warning("Cell %s upload rejected (permanent): %s", cell.id, reason)
            delivery.journal(RUN_ERROR, reason)
            self.db.finish_run(run_id, self._now(), RUN_ERROR,
                               rows_note=str(rows_built), message=reason)
            return RunResult(cell.id, RUN_ERROR, rows_built=rows_built,
                             file_path=str(file_path), run_id=run_id, message=reason)
        except Exception as e:  # noqa: BLE001 - transient/unknown: stage for retry
            reason = error_text(e)
            log.warning("Cell %s upload failed: %s", cell.id, reason)
            return self._stage_pending(cell, delivery, run_id, file_path, rows_built, reason)

        delivery.record_success(file_path.name, rows_built, _file_url(data))
        self.db.finish_run(run_id, self._now(), RUN_OK, rows_sent=rows_built,
                           rows_note=str(rows_built), message=success_msg)
        return RunResult(cell.id, RUN_OK, rows_built=rows_built, rows_sent=rows_built,
                         file_path=str(file_path), run_id=run_id, posted=True,
                         message=success_msg)

    def _stage_pending(self, cell: Cell, delivery: Delivery, run_id: int,
                       file_path: Path, rows_built: int, reason: str) -> RunResult:
        """Stage a built-but-unsent file for later retry.

        Returns FAIL (retryable via `retry_pending`) when the file is staged, or ERROR
        when even staging the pending file fails (nothing to retry). Shared by the
        upload-threw path and the network-offline pre-check.
        """
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

    # --- helpers ---------------------------------------------------------
    def _email_config(self, cell: Cell) -> tuple[EmailConfig | None, str]:
        """EmailConfig for an e-mail cell, or (None, actionable RU reason)."""
        if cell.email_account_id is None:
            return None, "У ячейки не выбран почтовый ящик для отправки."
        account = self.db.get_email_account(cell.email_account_id)
        if account is None:
            return None, "Почтовый ящик ячейки не найден."
        recipients = parse_recipients(cell.email_to)
        if not recipients:
            return None, "У ячейки не указан адрес получателя прайса."
        if not account.smtp_host or not account.login:
            return None, ("У почтового ящика не заданы SMTP-сервер или логин — "
                          "проверьте настройки на вкладке «Почта».")
        password = self.db.get_email_account_password(account.id)
        if not password:
            return None, ("У почтового ящика не задан пароль — введите его на "
                          "вкладке «Почта» (для Mail.ru/Яндекс/Gmail — пароль "
                          "приложения).")
        return EmailConfig(
            smtp_host=account.smtp_host, smtp_port=account.smtp_port,
            security=account.security, login=account.login, password=password,
            from_addr=account.from_addr or account.login,
            to_addrs=recipients, subject=cell.email_subject), ""

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
