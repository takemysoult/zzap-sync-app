"""SchedulerService — фоновые плановые выгрузки + офлайн-восстановление (Phase 4).

Модель (PROJECT_MEMORY §8): APScheduler ВНУТРИ приложения (не Планировщик заданий
Windows). Бизнес-логики здесь нет — переиспользуется `CellRunner`. Сервис только
решает «когда запускать» и «что делать при сбоях/простое».

Что обеспечивает:
  - интервальная задача (каждые N часов) выполняет все включённые ячейки;
  - **catch-up**: если ПК был выключен/спал и плановый запуск пропущен — при старте и
    при пробуждении выгрузка идёт НЕМЕДЛЕННО (а не ждёт следующего тика). Реализовано
    двумя независимыми путями: APScheduler `coalesce=True, misfire_grace_time=None`
    (процесс жил, но поток спал) И явная проверка просрочки при `start()` (процесс был
    выключен совсем) — см. `is_overdue`;
  - **досыл отложенного**: частый проход `flush_pending` дошлёт файлы, отложенные при
    отсутствии сети, как только связь вернётся (`RESEND_OK`);
  - живое изменение интервала без перезапуска (`reschedule`);
  - запуск «по требованию» из трея, не блокируя UI (одноразовые задачи).

Потоки: APScheduler `BackgroundScheduler` выполняет задачи на СВОИХ рабочих потоках
(не на UI). Каждая задача открывает свой `Database` (DAL потоко-аффинный; WAL +
busy_timeout из Phase 2 делают это безопасным), а `Com1C` сам делает
CoInitialize/CoUninitialize. Результат доставляется в UI через `listener` (GUI
эмитит Qt-сигнал → очередь на UI-поток, как в `AsyncRunner`).

Сервис Qt-независим и полностью тестируется: планировщик инжектируется (тесты
подставляют фейк — без реальных таймеров), как и фабрика `CellRunner`.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

from ..db.dal import Database
from .cell_runner import (RUN_ERROR, RUN_FAIL, RUN_OK, RUN_RESEND_OK,
                          CellRunner, RunResult)

log = logging.getLogger(__name__)

# Settings keys (defined here so the Qt-free service layer owns them; the GUI imports
# them from here rather than the other way round).
SETTING_INTERVAL_HOURS = "interval_hours"
DEFAULT_INTERVAL_HOURS = 5

FLUSH_MINUTES = 10
JOB_MAIN = "zzap_main_interval"
JOB_FLUSH = "zzap_pending_flush"

# RunSummary.kind values.
KIND_SCHEDULED = "scheduled"
KIND_MANUAL = "manual"
KIND_CATCHUP = "catchup"
KIND_FLUSH = "flush"

DbFactory = Callable[[], Database]
RunnerFactory = Callable[[Database], CellRunner]


@dataclass
class RunSummary:
    """Outcome of one batch (scheduled tick / manual run / catch-up / flush)."""
    kind: str
    results: list[RunResult] = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""
    skipped: bool = False        # flush skipped because busy or offline

    def _count(self, *statuses: str) -> int:
        return sum(1 for r in self.results if r.status in statuses)

    @property
    def posted(self) -> int:
        return sum(1 for r in self.results if r.posted)

    @property
    def ok(self) -> int:
        return self._count(RUN_OK, RUN_RESEND_OK)

    @property
    def failed(self) -> int:
        return self._count(RUN_FAIL)

    @property
    def errors(self) -> int:
        return self._count(RUN_ERROR)


# ----------------------------------------------------------------------
# Pure catch-up decision (no scheduler/timer/Qt — trivially unit-testable)
# ----------------------------------------------------------------------
def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def last_run_at(db: Database) -> datetime | None:
    """Most recent run start across all cells (scheduled OR manual), or None."""
    runs = db.list_runs(limit=1)
    return _parse_dt(runs[0].started_at) if runs else None


def is_overdue(last: datetime | None, interval_hours: int, now: datetime) -> bool:
    """True if a scheduled run is due now: never ran, or the interval has elapsed.

    An unparseable/absent last time counts as overdue so a fresh install (or a long
    outage) uploads immediately on start instead of waiting a whole interval.
    """
    if last is None:
        return True
    return (now - last) >= timedelta(hours=interval_hours)


class SchedulerService:
    def __init__(self, db_factory: DbFactory, work_dir: str | Path, *,
                 scheduler=None,
                 network_check: Callable[[], bool] | None = None,
                 listener: Callable[[RunSummary], None] | None = None,
                 runner_factory: RunnerFactory | None = None,
                 now: Callable[[], datetime] = datetime.now) -> None:
        self._db_factory = db_factory
        self._work_dir = Path(work_dir)
        self._scheduler = scheduler or BackgroundScheduler()
        self._network_check = network_check
        self._listener = listener
        self._runner_factory = runner_factory or self._default_runner_factory
        self._now = now
        # Serialises every run path (scheduled tick, manual, catch-up, flush) so two
        # batches never interleave COM / double-upload the same cell.
        self._run_lock = threading.Lock()

    # --- lifecycle --------------------------------------------------------
    def start(self) -> None:
        """Schedule the interval + flush jobs, start the scheduler, and catch up if
        a planned run was missed while the PC was off."""
        interval = self._interval_hours()
        self._scheduler.add_job(
            self._tick, IntervalTrigger(hours=interval), id=JOB_MAIN,
            name="ZZap: плановая выгрузка", coalesce=True, max_instances=1,
            misfire_grace_time=None, replace_existing=True)
        self._scheduler.add_job(
            self._flush_tick, IntervalTrigger(minutes=FLUSH_MINUTES), id=JOB_FLUSH,
            name="ZZap: досыл отложенного", coalesce=True, max_instances=1,
            replace_existing=True)
        if not getattr(self._scheduler, "running", False):
            self._scheduler.start()
        self._startup_catchup_if_overdue(interval)

    def set_listener(self, listener: Callable[[RunSummary], None] | None) -> None:
        """Set the run-completion callback after construction (the GUI wires its Qt
        signal bridge here once the tray exists)."""
        self._listener = listener

    def shutdown(self, wait: bool = False) -> None:
        try:
            if getattr(self._scheduler, "running", False):
                self._scheduler.shutdown(wait=wait)
        except Exception as e:  # noqa: BLE001 - best-effort on app exit
            log.warning("scheduler shutdown error: %s", e)

    def reschedule(self, interval_hours: int) -> None:
        """Apply a new interval live (no restart). No-op until the job exists."""
        interval = max(1, int(interval_hours))
        if self._scheduler.get_job(JOB_MAIN) is None:
            return  # not started yet; start() will use the saved interval
        self._scheduler.reschedule_job(JOB_MAIN, trigger=IntervalTrigger(hours=interval))
        log.info("Интервал выгрузки изменён на %dч (без перезапуска).", interval)

    @property
    def next_run_time(self) -> datetime | None:
        job = self._scheduler.get_job(JOB_MAIN)
        return getattr(job, "next_run_time", None) if job else None

    @property
    def running(self) -> bool:
        return bool(getattr(self._scheduler, "running", False))

    # --- run primitives (lock-serialised, Qt-free, return RunSummary) -----
    def run_all_now(self, kind: str = KIND_MANUAL) -> RunSummary:
        with self._run_lock:
            return self._execute(kind, cell_id=None)

    def run_cell_now(self, cell_id: int) -> RunSummary:
        with self._run_lock:
            return self._execute(KIND_MANUAL, cell_id=cell_id)

    def run_under_lock(self, fn: Callable[[], object]) -> object:
        """Run ``fn`` while holding the run-lock, so a manual run started from the open
        window can't overlap a scheduled tick. Used by the cells screen (which delivers
        its own result and intentionally does NOT emit a tray notification)."""
        with self._run_lock:
            return fn()

    def flush_pending(self) -> RunSummary:
        """Re-send every cell's pending file. Non-blocking: skips if a run is already
        in progress (the running batch supersedes pending anyway) or if offline."""
        if not self._run_lock.acquire(blocking=False):
            return RunSummary(kind=KIND_FLUSH, skipped=True)
        try:
            if self._network_check is not None and not self._network_check():
                return RunSummary(kind=KIND_FLUSH, skipped=True)
            started = self._now_iso()
            db = self._db_factory()
            results: list[RunResult] = []
            try:
                runner = self._runner_factory(db)
                for cell in db.list_cells():
                    res = runner.retry_pending(cell.id)
                    if res is not None:
                        results.append(res)
            finally:
                db.close()
        finally:
            self._run_lock.release()
        summary = RunSummary(kind=KIND_FLUSH, results=results, started_at=started,
                             finished_at=self._now_iso())
        if results:  # don't notify on an idle flush (it runs every few minutes)
            self._emit(summary)
        return summary

    # --- one-off requests (used by the tray; run off the UI thread) -------
    def request_run_all(self, kind: str = KIND_MANUAL) -> None:
        self._scheduler.add_job(self.run_all_now, DateTrigger(run_date=self._now()),
                                kwargs={"kind": kind}, misfire_grace_time=None)

    def request_run_cell(self, cell_id: int) -> None:
        self._scheduler.add_job(self.run_cell_now, DateTrigger(run_date=self._now()),
                                kwargs={"cell_id": cell_id}, misfire_grace_time=None)

    # --- scheduled job targets -------------------------------------------
    def _tick(self) -> None:
        self.run_all_now(kind=KIND_SCHEDULED)

    def _flush_tick(self) -> None:
        self.flush_pending()

    # --- internals --------------------------------------------------------
    def _execute(self, kind: str, cell_id: int | None) -> RunSummary:
        """Run the batch on a fresh Database, build a summary, notify the listener."""
        started = self._now_iso()
        db = self._db_factory()
        try:
            runner = self._runner_factory(db)
            if cell_id is None:
                results = runner.run_all_enabled()
            else:
                results = [runner.run_cell(cell_id)]
        finally:
            db.close()
        summary = RunSummary(kind=kind, results=results, started_at=started,
                             finished_at=self._now_iso())
        self._emit(summary)
        return summary

    def _startup_catchup_if_overdue(self, interval: int) -> None:
        db = self._db_factory()
        try:
            last = last_run_at(db)
        finally:
            db.close()
        if is_overdue(last, interval, self._now()):
            log.info("Catch-up: последняя выгрузка %s, интервал %dч — запускаю сразу.",
                     last, interval)
            self.request_run_all(kind=KIND_CATCHUP)

    def _interval_hours(self) -> int:
        db = self._db_factory()
        try:
            return max(1, db.get_int(SETTING_INTERVAL_HOURS, DEFAULT_INTERVAL_HOURS))
        finally:
            db.close()

    def _default_runner_factory(self, db: Database) -> CellRunner:
        return CellRunner(db, self._work_dir, network_check=self._network_check)

    def _emit(self, summary: RunSummary) -> None:
        if self._listener is None:
            return
        try:
            self._listener(summary)
        except Exception as e:  # noqa: BLE001 - a listener must never break a run
            log.warning("scheduler listener raised: %s", e)

    def _now_iso(self) -> str:
        return self._now().isoformat(timespec="seconds")
