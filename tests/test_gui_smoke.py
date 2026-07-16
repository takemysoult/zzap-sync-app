"""Headless smoke tests for the PySide2 GUI (Phase 3).

Run offscreen (no display) via QT_QPA_PLATFORM=offscreen. These verify the screens
construct against a real (temp) Database, the cell editor round-trips a Cell, the
duplicate banner lights up, and — security-critical — that AsyncRunner delivers a
worker's result on the UI thread and REDACTS secrets from a failing job's message.

Skipped automatically if PySide2 is unavailable.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide2")

from PySide2.QtCore import QThread  # noqa: E402
from PySide2.QtWidgets import QApplication  # noqa: E402

from app.db.models import Cabinet, Cell, Connection1C, EmailAccount  # noqa: E402
from app.gui.context import AppContext  # noqa: E402
from app.gui.main_window import MainWindow  # noqa: E402
from app.gui.screens.cell_editor import CellEditor  # noqa: E402
from app.gui.screens.cells import CellsScreen, _preview_text  # noqa: E402
from app.gui.screens.connection import ConnectionScreen  # noqa: E402
from app.services.cell_runner import PreviewResult  # noqa: E402
from app.gui.tray import TrayController  # noqa: E402
from app.gui.workers import AsyncRunner  # noqa: E402
from app.services.scheduler import (KIND_SCHEDULED, RunSummary,  # noqa: E402
                                     SchedulerService)
from conftest import FakeCipher  # noqa: E402


class _FakeSched:
    """Records jobs without firing anything (no real timers in the GUI smoke test)."""
    def __init__(self):
        self.running = False
        self.jobs: dict = {}

    def add_job(self, *a, **k):
        class _J:
            next_run_time = None
        if k.get("id"):
            self.jobs[k["id"]] = _J()
        return _J()

    def get_job(self, job_id):
        return self.jobs.get(job_id)

    def start(self, *a, **k):
        self.running = True

    def shutdown(self, *a, **k):
        self.running = False


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def ctx(tmp_path):
    c = AppContext(db_path=str(tmp_path / "t.db"), cipher=FakeCipher(),
                   work_dir=str(tmp_path / "work"))
    yield c
    c.close()


def _seed(ctx: AppContext):
    conn_id = ctx.db.add_connection(
        Connection1C(name="C1", kind="server", srvr="s", ref="r", usr="u",
                     is_default=True), "secret-pass")
    cab_id = ctx.db.add_cabinet(Cabinet(name="Каб1"), "zzap1_key")
    return conn_id, cab_id


def test_main_window_builds(qapp, ctx):
    _seed(ctx)
    win = MainWindow(ctx)
    assert win.tabs.count() == 6
    win.close()


def test_cell_editor_round_trips(qapp, ctx):
    conn_id, cab_id = _seed(ctx)
    cell = Cell(name="Ячейка А", enabled=True, connection_id=conn_id,
                cabinet_id=cab_id, code_templ=330017019, price_type="ZZap",
                warehouses=["Склад 1"])
    cell.id = ctx.db.add_cell(cell)
    editor = CellEditor(ctx, AsyncRunner(), ctx.db.get_cell(cell.id))
    out = editor.result_cell()
    assert out.name == "Ячейка А"
    assert out.code_templ == 330017019
    assert out.price_type == "ZZap"
    assert out.warehouses == ["Склад 1"]
    assert out.cabinet_id == cab_id


def test_new_cell_editor_defaults_to_disabled(qapp, ctx):
    _seed(ctx)
    editor = CellEditor(ctx, AsyncRunner(), None)
    assert editor.cb_enabled.isChecked() is False


def test_cell_editor_email_target_round_trips(qapp, ctx):
    conn_id, cab_id = _seed(ctx)
    acc_id = ctx.db.add_email_account(
        EmailAccount(name="Рабочая", smtp_host="smtp.mail.ru",
                     login="me@mail.ru"), password="app-pass")
    cell = Cell(name="Почтовая", enabled=True, connection_id=conn_id,
                cabinet_id=None, code_templ=0, price_type="ZZap",
                warehouses=["Склад 1"], target="email", email_account_id=acc_id,
                email_to="client@example.com", email_subject="Прайс")
    cell.id = ctx.db.add_cell(cell)
    editor = CellEditor(ctx, AsyncRunner(), ctx.db.get_cell(cell.id))
    # email target selected -> email fields visible, zzap fields hidden
    assert editor._current_target() == "email"
    assert editor.cmb_email.currentData() == acc_id
    out = editor.result_cell()
    assert out.target == "email"
    assert out.email_account_id == acc_id
    assert out.email_to == "client@example.com"
    assert out.email_subject == "Прайс"
    assert out.cabinet_id is None and out.code_templ == 0


def test_zzap_cell_editor_clears_email_fields(qapp, ctx):
    conn_id, cab_id = _seed(ctx)
    acc_id = ctx.db.add_email_account(
        EmailAccount(name="A", smtp_host="h", login="a@b.c"), password="p")
    cell = Cell(name="Я", enabled=True, connection_id=conn_id, cabinet_id=cab_id,
                code_templ=42, price_type="ZZap", warehouses=["Склад 1"],
                target="zzap", email_account_id=acc_id, email_to="x@y.z")
    cell.id = ctx.db.add_cell(cell)
    editor = CellEditor(ctx, AsyncRunner(), ctx.db.get_cell(cell.id))
    out = editor.result_cell()
    assert out.target == "zzap" and out.cabinet_id == cab_id
    assert out.email_account_id is None and out.email_to == ""


def test_connection_screen_odata_round_trip(qapp, ctx):
    screen = ConnectionScreen(ctx, AsyncRunner())
    screen.rb_odata.setChecked(True)
    screen.ed_name.setText("OData база")
    screen.ed_base.setText("http://host/base/odata/standard.odata")
    screen.ed_q_nom.setPlainText("Catalog_Номенклатура?$select=Ref_Key")
    screen.ed_usr.setText("webuser")
    screen.ed_pwd.setText("webpass")
    # Selecting OData shows its box and hides the COM box (visibility relative to parent,
    # which works without the top-level being shown).
    assert screen.odata_box.isVisibleTo(screen) is True
    assert screen.com_box.isVisibleTo(screen) is False
    screen._on_save()

    saved = ctx.db.get_default_connection()
    assert saved is not None
    assert saved.source == "odata"
    assert saved.odata_base_url.endswith("standard.odata")
    assert saved.odata_nomenclature_query.startswith("Catalog_Номенклатура")
    assert ctx.db.get_connection_password(saved.id) == "webpass"

    # reload re-selects OData and refills the fields (password stays masked)
    screen.reload()
    assert screen.rb_odata.isChecked()
    assert screen.ed_base.text().endswith("standard.odata")


def test_cell_editor_allows_empty_warehouse_for_odata(qapp, ctx):
    conn_id = ctx.db.add_connection(
        Connection1C(name="OD", source="odata",
                     odata_base_url="http://host/odata",
                     odata_nomenclature_query="Catalog_Номенклатура", usr="u"),
        "p")
    cab_id = ctx.db.add_cabinet(Cabinet(name="Каб1"), "zzap1_key")
    cell = Cell(name="OData cell", enabled=True, connection_id=conn_id,
                cabinet_id=cab_id, code_templ=42, price_type="", warehouses=[])
    cell.id = ctx.db.add_cell(cell)
    editor = CellEditor(ctx, AsyncRunner(), ctx.db.get_cell(cell.id))
    # OData selected -> warehouse/price controls disabled, note shown
    assert editor.btn_discover.isEnabled() is False
    assert editor.lst_wh.isEnabled() is False
    # _accept must pass despite no warehouse / no price type
    from PySide2.QtWidgets import QDialog
    editor._accept()
    assert editor.result() == QDialog.Accepted


def test_cells_screen_has_a_send_nothing_preview_button(qapp, ctx):
    _seed(ctx)
    screen = CellsScreen(ctx, AsyncRunner())
    assert screen.btn_preview.isEnabled()
    assert "без отправки" in screen.btn_preview.text()


def test_preview_text_flags_all_zero_quantities_as_unsendable():
    result = PreviewResult(cell_id=1, ok=True, rows=3, zero_quantity=3, zero_price=0,
                           file_path="p.xlsx")
    text, suspicious = _preview_text(result)
    assert suspicious is True
    assert "НЕЛЬЗЯ" in text                      # explicit "do not upload this"
    assert "количество" in text


def test_preview_text_flags_all_zero_prices():
    result = PreviewResult(cell_id=1, ok=True, rows=2, zero_quantity=0, zero_price=2,
                           file_path="p.xlsx")
    text, suspicious = _preview_text(result)
    assert suspicious is True
    assert "цена" in text


def test_preview_text_is_calm_when_data_looks_sane():
    result = PreviewResult(cell_id=1, ok=True, rows=100, zero_quantity=0, zero_price=3,
                           file_path="p.xlsx", sample=[["B", "A-1", "Д", 5, 10.0]])
    text, suspicious = _preview_text(result)
    assert suspicious is False
    assert "НЕЛЬЗЯ" not in text
    assert "В ZZap ничего не отправлено." in text


def test_duplicate_banner_lights_up(qapp, ctx):
    _, cab_id = _seed(ctx)
    common = dict(enabled=True, cabinet_id=cab_id, price_type="ZZap",
                  warehouses=["Общий склад"])
    ctx.db.add_cell(Cell(name="A", code_templ=111, **common))
    ctx.db.add_cell(Cell(name="B", code_templ=222, **common))
    win = MainWindow(ctx)
    win.cells.reload()
    # isHidden() reflects explicit setVisible() without needing a shown top-level.
    assert win.cells.banner.isHidden() is False
    assert "задвоение" in win.cells.banner.text()
    win.close()


def _drain(qapp, runner, predicate, timeout_ms=2000):
    """Let the worker finish, then pump the event loop until `predicate` is true."""
    runner.wait(timeout_ms)
    waited = 0
    while not predicate() and waited < timeout_ms:
        qapp.processEvents()
        QThread.msleep(10)
        waited += 10
    qapp.processEvents()


def test_async_runner_delivers_on_ui_thread(qapp):
    runner = AsyncRunner()
    main_thread = QThread.currentThread()
    captured: dict = {}

    def on_ok(result):
        captured["result"] = result
        captured["thread"] = QThread.currentThread()

    runner.submit(lambda: 2 + 2, on_ok=on_ok)
    _drain(qapp, runner, lambda: "result" in captured)
    assert captured["result"] == 4
    # The whole point: the callback must run on the UI thread, not the worker thread.
    assert captured["thread"] is main_thread


def test_async_runner_redacts_secret_on_failure(qapp):
    runner = AsyncRunner()
    errors: list[str] = []

    def boom():
        raise RuntimeError('Connect failed: Srvr="s";Usr="admin";Pwd="hunter2";')

    runner.submit(boom, on_ok=lambda r: None, on_err=errors.append)
    _drain(qapp, runner, lambda: bool(errors))
    assert errors, "error callback should have fired"
    assert "hunter2" not in errors[0]
    assert "admin" not in errors[0]
    assert 'Pwd="***"' in errors[0]


def test_tray_delivers_run_summary_on_ui_thread(qapp, ctx):
    # The Phase 4 tray bridge must deliver a scheduler RunSummary on the UI thread
    # (queued), exactly like AsyncRunner — a bare-closure/direct connection would run
    # _on_run_finished on the worker thread and touch widgets off-thread.
    import threading

    svc = SchedulerService(db_factory=ctx.new_db, work_dir=ctx.work_dir,
                           scheduler=_FakeSched())
    win = MainWindow(ctx, svc)
    tray = TrayController(ctx, svc, win)
    svc.set_listener(tray.listener)

    main_thread = QThread.currentThread()
    captured: dict = {}
    original = tray._on_run_finished

    def wrapped(summary):
        captured["thread"] = QThread.currentThread()
        captured["summary"] = summary
        original(summary)

    tray._bridge.run_finished.disconnect()
    tray._bridge.run_finished.connect(wrapped)

    threading.Thread(
        target=lambda: svc._emit(RunSummary(kind=KIND_SCHEDULED, results=[]))).start()
    waited = 0
    while "summary" not in captured and waited < 2000:
        qapp.processEvents()
        QThread.msleep(10)
        waited += 10
    qapp.processEvents()

    assert captured.get("summary") is not None
    assert captured["thread"] is main_thread     # delivered on the UI thread
    texts = [a.text() for a in tray._menu.actions()]
    assert any("Открыть окно" in t for t in texts)
    assert any("Выход" in t for t in texts)
    win.close()


def test_status_screen_shows_pending_indicator(qapp, ctx):
    from engine.delivery import Delivery

    from app.gui.screens.status import StatusScreen

    conn_id, cab_id = _seed(ctx)
    cell = Cell(name="C", enabled=True, connection_id=conn_id, cabinet_id=cab_id,
                code_templ=1, price_type="ZZap", warehouses=["W"])
    cell.id = ctx.db.add_cell(cell)

    # Stage a pending file for this cell (network was down at upload time).
    cell_dir = ctx.work_dir / f"cell_{cell.id}"
    cell_dir.mkdir(parents=True, exist_ok=True)
    built = cell_dir / "price.xlsx"
    built.write_bytes(b"xlsx")
    Delivery(cell_dir).mark_pending(built, "price.xlsx", "нет сети", rows=42)

    screen = StatusScreen(ctx)
    screen.cmb_cell.setCurrentIndex(screen.cmb_cell.findData(cell.id))
    assert screen.lbl_pending.isHidden() is False
    assert "Ожидает досылки" in screen.lbl_pending.text()
