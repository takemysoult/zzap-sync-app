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
                                       RUN_RESEND_OK)
from conftest import FakeCipher, make_rows
from engine.config import OdataConfig, ZzapConfig
from engine.delivery import Delivery
from engine.exclusions import read_exclusion_articles
from engine.models import PriceRow
from engine.zzap_client import ZzapPermanentError

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


def _seed_cell(db, *, enabled=True, code_templ=330017019,
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
        exclusion_list_id=exclusion_list_id))
    return cell_id, conn, cab


def _runner(db, tmp_path, *, rows=None, uploader=None):
    rows = make_rows() if rows is None else rows
    return CellRunner(db, tmp_path / "work",
                      source_factory=lambda cfg: FakeSource(rows),
                      uploader=uploader or RecordingUploader())


def test_real_upload_uses_cabinet_api_url_and_records_ok(db, tmp_path):
    # Guards against a wrong-host upload: the cabinet's stored api_url must flow through.
    custom = "https://other-host.example/api/client/v1/price1c/upload"
    cell_id, *_ = _seed_cell(db, api_url=custom)
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


def test_upload_failure_stages_pending_then_retry_resends(db, tmp_path):
    cell_id, *_ = _seed_cell(db)
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


def test_offline_network_check_stages_pending_without_posting(db, tmp_path):
    # Phase 4: a known-down network must NOT attempt the POST — the built file is
    # staged to pending and a later retry (network back) resends it.
    cell_id, *_ = _seed_cell(db)
    up = RecordingUploader()                     # would succeed if it were ever called
    runner = CellRunner(db, tmp_path / "work",
                        source_factory=lambda cfg: FakeSource(make_rows()),
                        uploader=up, network_check=lambda: False)
    res = runner.run_cell(cell_id)

    assert res.status == RUN_FAIL
    assert res.posted is False
    assert up.calls == []                        # no POST attempted while offline
    delivery = Delivery(tmp_path / "work" / f"cell_{cell_id}")
    assert delivery.get_pending() is not None

    ok = RecordingUploader()
    res2 = _runner(db, tmp_path, uploader=ok).retry_pending(cell_id)
    assert res2.status == RUN_RESEND_OK
    assert len(ok.calls) == 1
    assert delivery.get_pending() is None


def test_permanent_zzap_error_is_error_and_not_staged(db, tmp_path):
    # Phase 5: a permanent rejection (bad key / wrong url / rejected content) must NOT
    # be staged for endless retry — it's ERROR with the actionable message, no pending.
    cell_id, *_ = _seed_cell(db)
    bad = RecordingUploader(error=ZzapPermanentError("ZZap вернул 401 — неверный ключ."))
    res = _runner(db, tmp_path, uploader=bad).run_cell(cell_id)

    assert res.status == RUN_ERROR
    assert res.posted is False
    assert len(bad.calls) == 1                   # it was attempted...
    delivery = Delivery(tmp_path / "work" / f"cell_{cell_id}")
    assert delivery.get_pending() is None        # ...but nothing left to retry


def test_zero_rows_real_send_is_blocked(db, tmp_path):
    # Phase 5 safety: 0 rows would WIPE the template (uploads fully replace) -> refuse.
    cell_id, *_ = _seed_cell(db)
    up = RecordingUploader()
    res = _runner(db, tmp_path, rows=[], uploader=up).run_cell(cell_id)

    assert res.status == RUN_ERROR
    assert up.calls == []                        # nothing posted
    assert "0 строк" in res.message
    assert db.list_runs(cell_id=cell_id)[0].status == RUN_ERROR


def test_missing_warehouses_is_error_and_sends_nothing(db, tmp_path):
    cell_id, *_ = _seed_cell(db, warehouses=[])
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

    cell_id, *_ = _seed_cell(db)
    db.assign_exclusion_list_to_cell(cell_id, list_id)
    res = _runner(db, tmp_path).run_cell(cell_id)

    assert res.status == RUN_OK   # uploaded (file built then sent)
    numbers = [row[1] for row in load_workbook(res.file_path).active.iter_rows(values_only=True)]
    assert numbers == ["B-2"]   # A-1 excluded; only B-2 remains
    assert res.rows_built == 1


def test_odata_cell_uses_odata_source_and_skips_warehouse_query(db, tmp_path):
    # An OData connection reads via its own queries — the cell needs no warehouse/
    # price type, and the COM source must not be touched. The built OdataConfig must
    # carry the connection's base_url + credentials.
    conn = db.add_connection(
        Connection1C(name="od", source="odata",
                     odata_base_url="http://host/base/odata/standard.odata",
                     odata_nomenclature_query="Catalog_Номенклатура?$select=Ref_Key",
                     usr="webuser"),
        password="webpass")
    cab = db.add_cabinet(Cabinet(name="Cab"), api_key="zzap1_key")
    cell_id = db.add_cell(Cell(
        name="OData cell", enabled=True, connection_id=conn, cabinet_id=cab,
        code_templ=42, price_type="", warehouses=[]))   # no warehouse/price at all

    captured = {}

    def odata_factory(cfg):
        captured["cfg"] = cfg
        return FakeSource(make_rows())

    def com_must_not_run(cfg):
        raise AssertionError("COM source used for an OData cell")

    up = RecordingUploader()
    runner = CellRunner(db, tmp_path / "work",
                        source_factory=com_must_not_run,
                        odata_source_factory=odata_factory, uploader=up)
    res = runner.run_cell(cell_id)

    assert res.status == RUN_OK
    assert res.posted is True
    assert res.rows_sent == 2                         # make_rows -> 2 after clean_rows
    cfg = captured["cfg"]
    assert isinstance(cfg, OdataConfig)
    assert cfg.base_url == "http://host/base/odata/standard.odata"
    assert cfg.username == "webuser" and cfg.password == "webpass"
    assert up.calls[0]["cfg"].code_templ == 42


# --- preview / dry run («Собрать файл без отправки») ------------------------
def test_build_preview_builds_file_and_sends_nothing(db, tmp_path):
    cell_id, *_ = _seed_cell(db)
    up = RecordingUploader()
    res = _runner(db, tmp_path, uploader=up).build_preview(cell_id)

    assert res.ok is True
    assert res.rows == 2                          # make_rows -> 2 after clean_rows
    assert res.zero_quantity == 0 and res.zero_price == 0
    assert res.file_path.endswith("preview.xlsx")
    assert len(res.sample) == 2

    # nothing was sent, nothing was journalled, nothing queued for delivery
    assert up.calls == []
    assert db.list_runs(cell_id=cell_id) == []
    assert Delivery(tmp_path / "work" / f"cell_{cell_id}").get_pending() is None
    # and the real upload artefact was NOT produced
    assert not (tmp_path / "work" / f"cell_{cell_id}" / "price.xlsx").exists()


def test_build_preview_counts_zero_quantities(db, tmp_path):
    # The OData wrong-field-name signature: rows exist, every quantity is 0.
    rows = [PriceRow("BrandA", "A-1", "Деталь 1", 0, 100.0),
            PriceRow("BrandB", "B-2", "Деталь 2", 0, 50.5)]
    cell_id, *_ = _seed_cell(db)
    res = _runner(db, tmp_path, rows=rows).build_preview(cell_id)

    assert res.ok is True
    assert res.rows == 2
    assert res.zero_quantity == 2                 # caller must refuse to upload this
    assert res.zero_price == 0


def test_build_preview_does_not_need_a_cabinet(db, tmp_path):
    # A connection can be validated before the ZZap cabinet is even configured.
    conn = db.add_connection(Connection1C(name="b", srvr="s", ref="r"), password="p")
    cell_id = db.add_cell(Cell(name="c", connection_id=conn, cabinet_id=None,
                               code_templ=0, price_type="ZZap", warehouses=["W"]))
    res = _runner(db, tmp_path).build_preview(cell_id)

    assert res.ok is True and res.rows == 2


def test_build_preview_reports_error_without_raising(db, tmp_path):
    cab = db.add_cabinet(Cabinet(name="Cab"), api_key="k")
    cell_id = db.add_cell(Cell(name="no conn", cabinet_id=cab, code_templ=1,
                               price_type="ZZap", warehouses=["W"]))
    res = _runner(db, tmp_path).build_preview(cell_id)

    assert res.ok is False
    assert "подключение" in res.message.lower()
    assert db.list_runs(cell_id=cell_id) == []    # a failed preview is not a run


def test_build_preview_of_unknown_cell_returns_result_not_exception(db, tmp_path):
    # The child process must always leave a readable result, even for a bad id.
    res = _runner(db, tmp_path).build_preview(4242)
    assert res.ok is False
    assert res.cell_id == 4242
    assert "не найдена" in res.message


def test_build_preview_does_not_disturb_a_pending_file(db, tmp_path):
    # A previous run failed and staged price.xlsx for retry; a preview must not eat it.
    cell_id, *_ = _seed_cell(db)
    failing = RecordingUploader(error=RuntimeError("ZZap вернул 500"))
    _runner(db, tmp_path, uploader=failing).run_cell(cell_id)
    delivery = Delivery(tmp_path / "work" / f"cell_{cell_id}")
    assert delivery.get_pending() is not None

    _runner(db, tmp_path).build_preview(cell_id)
    assert delivery.get_pending() is not None     # still there, still retryable


def test_run_all_enabled_runs_only_enabled_cells(db, tmp_path):
    c1, conn, cab = _seed_cell(db, enabled=True)
    db.add_cell(Cell(name="off", enabled=False, connection_id=conn, cabinet_id=cab,
                     code_templ=1, price_type="ZZap", warehouses=["W"]))
    results = _runner(db, tmp_path).run_all_enabled()

    assert [r.cell_id for r in results] == [c1]
    assert results[0].status == RUN_OK
