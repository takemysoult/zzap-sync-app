"""SchedulerService — scheduling, live reschedule, offline catch-up and pending flush.

No real timers and no live 1C/ZZap: a FakeScheduler records job wiring (so we can
assert the catch-up settings without anything firing), and the run primitives are
driven over a seeded temp Database with an injected fake 1C source + uploader.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from app.db.dal import Database
from app.db.models import Cabinet, Cell, Connection1C, RunHistory
from app.services.cell_runner import RUN_RESEND_OK, CellRunner
from app.services.scheduler import (JOB_FLUSH, JOB_MAIN, KIND_CATCHUP, SchedulerService,
                                     is_overdue, last_run_at)
from apscheduler.triggers.interval import IntervalTrigger
from conftest import FakeCipher, make_rows


# --- fakes ------------------------------------------------------------------
class FakeSource:
    def fetch_rows(self):
        return make_rows()


class RecUp:
    """Records calls; raises if `.error` is set (lets one instance fail then succeed)."""
    def __init__(self):
        self.calls = []
        self.error = None

    def __call__(self, cfg, file_path, file_name=None, *, session=None):
        self.calls.append((cfg, str(file_path), file_name))
        if self.error:
            raise self.error
        return {"success": True, "result": {"file_url": "http://zzap/f.xlsx"}}


class FakeJob:
    def __init__(self, job_id, trigger, options):
        self.id = job_id
        self.trigger = trigger
        self.options = options
        self.next_run_time = None


class FakeScheduler:
    """Records add/reschedule without running anything (no real timers)."""
    def __init__(self):
        self.jobs: dict[str, FakeJob] = {}
        self.date_jobs: list[dict] = []      # one-off requests (catch-up / tray runs)
        self.rescheduled: dict[str, object] = {}
        self.running = False

    def add_job(self, func, trigger=None, id=None, **kwargs):
        if id is None:                       # one-off date job (request_run_*)
            self.date_jobs.append({"func": func, "trigger": trigger,
                                   "kwargs": kwargs.get("kwargs", {})})
            return FakeJob(None, trigger, kwargs)
        job = FakeJob(id, trigger, kwargs)
        self.jobs[id] = job
        return job

    def reschedule_job(self, job_id, trigger=None):
        self.rescheduled[job_id] = trigger
        if job_id in self.jobs:
            self.jobs[job_id].trigger = trigger

    def get_job(self, job_id):
        return self.jobs.get(job_id)

    def remove_job(self, job_id):
        self.jobs.pop(job_id, None)

    def start(self, paused=False):
        self.running = True

    def shutdown(self, wait=False):
        self.running = False


# --- helpers ----------------------------------------------------------------
def _db(db_path):
    return Database(db_path, FakeCipher())


def _seed(db_path, *, staging, enabled=True):
    db = _db(db_path)
    try:
        conn = db.add_connection(
            Connection1C(name="b", kind="server", srvr="s", ref="r", usr="u"),
            password="p")
        cab = db.add_cabinet(Cabinet(name="Cab"), api_key="zzap1_key")
        return db.add_cell(Cell(name="C", enabled=enabled, connection_id=conn,
                                cabinet_id=cab, code_templ=330017019, price_type="ZZap",
                                warehouses=["W"], staging_mode=staging))
    finally:
        db.close()


def _insert_run(db_path, cell_id, started_at):
    db = _db(db_path)
    try:
        db.add_run(RunHistory(cell_id=cell_id, started_at=started_at, status="OK"))
    finally:
        db.close()


def _service(db_path, work_dir, *, scheduler=None, network_check=None, uploader=None):
    up = uploader if uploader is not None else RecUp()

    def runner_factory(db):
        return CellRunner(db, work_dir, source_factory=lambda cfg: FakeSource(),
                          uploader=up)

    svc = SchedulerService(db_factory=lambda: _db(db_path), work_dir=work_dir,
                           scheduler=scheduler or FakeScheduler(),
                           network_check=network_check, runner_factory=runner_factory)
    return svc, up


# --- pure catch-up decision -------------------------------------------------
def test_is_overdue_truth_table():
    now = datetime(2026, 6, 24, 12, 0, 0)
    assert is_overdue(None, 5, now) is True                       # never ran
    assert is_overdue(now - timedelta(hours=6), 5, now) is True   # past interval
    assert is_overdue(now - timedelta(hours=5), 5, now) is True   # exactly at interval
    assert is_overdue(now - timedelta(hours=1), 5, now) is False  # recent


def test_last_run_at_reads_most_recent(tmp_path):
    db_path = str(tmp_path / "a.db")
    cid = _seed(db_path, staging=True)
    assert last_run_at(_db(db_path)) is None
    _insert_run(db_path, cid, "2026-06-24T09:00:00")
    _insert_run(db_path, cid, "2026-06-24T11:30:00")
    assert last_run_at(_db(db_path)) == datetime(2026, 6, 24, 11, 30, 0)


# --- scheduling wiring ------------------------------------------------------
def test_start_adds_main_and_flush_jobs_with_catchup_settings(tmp_path):
    db_path = str(tmp_path / "a.db")
    _seed(db_path, staging=True)
    fake = FakeScheduler()
    svc, _ = _service(db_path, tmp_path / "work", scheduler=fake)
    svc.start()

    main = fake.jobs[JOB_MAIN]
    assert main.trigger.interval == timedelta(hours=5)   # default interval
    assert main.options["coalesce"] is True
    assert main.options["max_instances"] == 1
    assert main.options["misfire_grace_time"] is None    # run no matter how late
    assert JOB_FLUSH in fake.jobs
    assert fake.running is True


def test_reschedule_swaps_main_trigger_live(tmp_path):
    db_path = str(tmp_path / "a.db")
    _seed(db_path, staging=True)
    fake = FakeScheduler()
    svc, _ = _service(db_path, tmp_path / "work", scheduler=fake)
    svc.start()
    svc.reschedule(8)
    assert isinstance(fake.rescheduled[JOB_MAIN], IntervalTrigger)
    assert fake.rescheduled[JOB_MAIN].interval == timedelta(hours=8)


def test_reschedule_noop_before_start(tmp_path):
    db_path = str(tmp_path / "a.db")
    _seed(db_path, staging=True)
    fake = FakeScheduler()
    svc, _ = _service(db_path, tmp_path / "work", scheduler=fake)
    svc.reschedule(8)                       # job not added yet
    assert fake.rescheduled == {}


# --- offline catch-up (PC was off) ------------------------------------------
def test_startup_catchup_runs_immediately_when_overdue(tmp_path):
    db_path = str(tmp_path / "a.db")
    cid = _seed(db_path, staging=True)
    _insert_run(db_path, cid,
                (datetime.now() - timedelta(hours=10)).isoformat(timespec="seconds"))
    fake = FakeScheduler()
    svc, _ = _service(db_path, tmp_path / "work", scheduler=fake)
    svc.start()
    kinds = [j["kwargs"].get("kind") for j in fake.date_jobs]
    assert KIND_CATCHUP in kinds


def test_no_catchup_when_recent(tmp_path):
    db_path = str(tmp_path / "a.db")
    cid = _seed(db_path, staging=True)
    _insert_run(db_path, cid, datetime.now().isoformat(timespec="seconds"))
    fake = FakeScheduler()
    svc, _ = _service(db_path, tmp_path / "work", scheduler=fake)
    svc.start()
    assert fake.date_jobs == []


# --- run primitives ---------------------------------------------------------
def test_run_all_now_respects_staging(tmp_path):
    db_path = str(tmp_path / "a.db")
    _seed(db_path, staging=True)
    svc, up = _service(db_path, tmp_path / "work")
    summary = svc.run_all_now()
    assert summary.staged == 1
    assert summary.posted == 0
    assert up.calls == []                   # nothing sent in staging


def test_run_all_now_posts_a_real_cell(tmp_path):
    db_path = str(tmp_path / "a.db")
    _seed(db_path, staging=False)
    svc, up = _service(db_path, tmp_path / "work")
    summary = svc.run_all_now()
    assert summary.posted == 1
    assert summary.ok == 1
    assert len(up.calls) == 1


# --- pending flush (network was down, then back) ----------------------------
def test_flush_skips_when_offline(tmp_path):
    db_path = str(tmp_path / "a.db")
    _seed(db_path, staging=False)
    svc, up = _service(db_path, tmp_path / "work", network_check=lambda: False)
    summary = svc.flush_pending()
    assert summary.skipped is True
    assert up.calls == []                   # never even opened a connection


def test_flush_skips_when_a_run_holds_the_lock(tmp_path):
    db_path = str(tmp_path / "a.db")
    _seed(db_path, staging=True)
    svc, _ = _service(db_path, tmp_path / "work")
    svc._run_lock.acquire()
    try:
        assert svc.flush_pending().skipped is True
    finally:
        svc._run_lock.release()


def test_flush_resends_pending_once_network_returns(tmp_path):
    db_path = str(tmp_path / "a.db")
    _seed(db_path, staging=False)
    up = RecUp()
    svc, _ = _service(db_path, tmp_path / "work", uploader=up)

    up.error = RuntimeError("ZZap 500")     # network/server down at upload time
    fail = svc.run_all_now()
    assert fail.failed == 1

    up.error = None                          # network back
    resend = svc.flush_pending()
    assert resend.posted == 1
    assert any(r.status == RUN_RESEND_OK for r in resend.results)
