"""CellRunner end-to-end behavior (no live 1C/ZZap — source + uploader injected).

Covers the safety gate (per-cell + global staging), the real-upload path and that
the cabinet's stored api_url flows into the ZzapConfig, upload-failure -> pending ->
retry, the ERROR path, exclusions applied from an imported Excel list, and
run_all_enabled.
"""
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook

from app.db.dal import Database
from app.db.models import Cabinet, Cell, Connection1C
from app.services.cell_runner import (CellRunner, RUN_ERROR, RUN_FAIL, RUN_OK,
                                       RUN_RESEND_OK, RUN_STAGED)
from conftest import FakeCipher, make_rows
from engine.config import ZzapConfig
from engine.delivery import Delivery
from engine.exclusions import read_exclusion_articles

_DEFAULT_URL = "https://b52-api.zzap.pro/api/client/v1/price1c/upload"


class FakeSource:
    def __init__(self, rows):
        self._rows = rows

    def fetch_rows(self):
        return list(self._rows)


class RecordingUploader:
    """Stand-in for engine.zzap_client.upload_price; records calls, can raise."""

    def __init__(self, response=None, error=None):
        self.calls = []
        self.error = error
        self.response = response or {"success": True,
                                     "result": {"file_url": "http://zzap/f.xlsx"}}

    def __call__(self, cfg, file_path, file_name=None, *, session=None):
        self.calls.append({"cfg": cfg, "file_path": str(file_path),
                           "file_name": file_name})
        if self.error:
            raise self.error
        return self.response


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "app.db", cipher=FakeCipher())
    try:
        yield database
    finally:
        database.close()


def _seed_cell(db, *, staging=False, enabled=True, code_templ=330017019,
               warehouses=None, api_url=None, api_key="zzap1_key",
               exclusion_list_id=None):
    conn = db.add_connection(
        Connection1C(name="base", kind="server", srvr="Serv1C", ref="ut2025",
                     usr="Натали"), password="p@ss")
    cab = db.add_cabinet(Cabinet(name="Cab", api_url=api_url or _DEFAULT_URL),
                         api_key=api_key)
    cell_id = db.add_cell(Cell(
        name="Cell A", enabled=enabled, connection_id=conn, cabinet_id=cab,
        code_templ=code_templ, price_type="ZZap",
        warehouses=warehouses if warehouses is not None else ["Квант (Новые) 3 этаж"],
        staging_mode=staging, exclusion_list_id=exclusion_list_id))
    return cell_id, conn, cab


def _runner(db, tmp_path, *, rows=None, uploader=None):
    rows = make_rows() if rows is None else rows
    return CellRunner(db, tmp_path / "work",
                      source_factory=lambda cfg: FakeSource(rows),
                      uploader=uploader or RecordingUploader())


def test_staging_builds_file_but_does_not_post(db, tmp_path):
    cell_id, *_ = _seed_cell(db, staging=True)
    up = RecordingUploader()
    res = _runner(db, tmp_path, uploader=up).run_cell(cell_id)

    assert res.status == RUN_STAGED
    assert res.posted is False
    assert up.calls == []                       # nothing sent
    assert Path(res.file_path).exists()         # but the file was built
    assert db.list_runs(cell_id=cell_id)[0].status == RUN_STAGED


def test_real_upload_uses_cabinet_api_url_and_records_ok(db, tmp_path):
    # Guards against a wrong-host upload: the cabinet's stored api_url must flow through.
    custom = "https://other-host.example/api/client/v1/price1c/upload"
    cell_id, *_ = _seed_cell(db, staging=False, api_url=custom)
    up = RecordingUploader()
    res = _runner(db, tmp_path, uploader=up).run_cell(cell_id)

    assert res.status == RUN_OK
    assert res.posted is True
    assert res.rows_sent == 2                    # make_rows -> 2 after clean_rows
    assert len(up.calls) == 1
    cfg = up.calls[0]["cfg"]
    assert isinstance(cfg, ZzapConfig)
    assert cfg.api_url == custom
    assert cfg.code_templ == 330017019
    assert cfg.test_mode is False
    run = db.list_runs(cell_id=cell_id)[0]
    assert run.status == RUN_OK and run.rows_sent == 2


def test_global_staging_kill_switch_blocks_a_real_cell(db, tmp_path):
    cell_id, *_ = _seed_cell(db, staging=False)
    db.set_bool("staging_mode", True)            # global kill-switch ON
    up = RecordingUploader()
    res = _runner(db, tmp_path, uploader=up).run_cell(cell_id)

    assert res.status == RUN_STAGED
    assert up.calls == []


def test_upload_failure_stages_pending_then_retry_resends(db, tmp_path):
    cell_id, *_ = _seed_cell(db, staging=False)
    failing = RecordingUploader(error=RuntimeError("ZZap вернул 500"))
    res = _runner(db, tmp_path, uploader=failing).run_cell(cell_id)

    assert res.status == RUN_FAIL
    assert res.posted is False
    delivery = Delivery(tmp_path / "work" / f"cell_{cell_id}")
    assert delivery.get_pending() is not None

    ok = RecordingUploader()
    res2 = _runner(db, tmp_path, uploader=ok).retry_pending(cell_id)
    assert res2.status == RUN_RESEND_OK
    assert res2.posted is True
    assert len(ok.calls) == 1
    assert delivery.get_pending() is None        # cleared after a successful resend
    statuses = {r.status for r in db.list_runs(cell_id=cell_id)}
    assert {RUN_FAIL, RUN_RESEND_OK} <= statuses


def test_missing_warehouses_is_error_and_sends_nothing(db, tmp_path):
    cell_id, *_ = _seed_cell(db, staging=False, warehouses=[])
    up = RecordingUploader()
    res = _runner(db, tmp_path, uploader=up).run_cell(cell_id)

    assert res.status == RUN_ERROR
    assert up.calls == []
    assert db.list_runs(cell_id=cell_id)[0].status == RUN_ERROR


def test_imported_excel_exclusions_filter_a_run(db, tmp_path):
    # The PROMPT acceptance: read an .xlsx -> import list -> assign to a cell ->
    # the cell run actually filters those articles.
    xls = tmp_path / "excl.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.append(["Артикул"])      # header
    ws.append(["a-1"])          # matches make_rows() row "A-1" (case-insensitive)
    ws.append([""])             # blank — dropped
    ws.append(["zzz-unused"])
    wb.save(xls)

    articles = read_exclusion_articles(xls, column=0, skip_header=True)
    list_id = db.import_exclusion_list(articles, name="из Excel")
    assert "A-1" in db.get_exclusion_list(list_id).articles.splitlines()

    cell_id, *_ = _seed_cell(db, staging=True)
    db.assign_exclusion_list_to_cell(cell_id, list_id)
    res = _runner(db, tmp_path).run_cell(cell_id)

    assert res.status == RUN_STAGED
    numbers = [row[1] for row in load_workbook(res.file_path).active.iter_rows(values_only=True)]
    assert numbers == ["B-2"]   # A-1 excluded; only B-2 remains
    assert res.rows_built == 1


def test_run_all_enabled_runs_only_enabled_cells(db, tmp_path):
    c1, conn, cab = _seed_cell(db, staging=True, enabled=True)
    db.add_cell(Cell(name="off", enabled=False, connection_id=conn, cabinet_id=cab,
                     code_templ=1, price_type="ZZap", warehouses=["W"],
                     staging_mode=True))
    results = _runner(db, tmp_path).run_all_enabled()

    assert [r.cell_id for r in results] == [c1]
    assert results[0].status == RUN_STAGED
