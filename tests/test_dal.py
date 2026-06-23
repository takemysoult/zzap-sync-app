import pytest

from app.db.dal import Database
from app.db.models import Cabinet, Cell, Connection1C, RunHistory
from conftest import FakeCipher


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "test.db", cipher=FakeCipher())
    try:
        yield database
    finally:
        database.close()


def test_schema_version_applied(db):
    version = db._conn.execute("PRAGMA user_version").fetchone()[0]
    assert version == 1


def test_cabinet_crud_secret_encrypted_at_rest(db):
    cid = db.add_cabinet(Cabinet(name="Cab1"), api_key="zzap1_secretkey")
    cab = db.get_cabinet(cid)
    assert cab.name == "Cab1"
    assert cab.api_url.endswith("/price1c/upload")   # correct default endpoint
    assert cab.has_api_key is True
    assert db.get_cabinet_api_key(cid) == "zzap1_secretkey"
    # stored blob must NOT contain the plaintext key
    raw = db._conn.execute(
        "SELECT api_key_enc FROM cabinet WHERE id=?", (cid,)).fetchone()[0]
    assert b"zzap1_secretkey" not in bytes(raw)


def test_connection_password_preserved_on_partial_update(db):
    cid = db.add_connection(
        Connection1C(name="base1", srvr="Serv1C", ref="ut2025", usr="Натали",
                     is_default=True),
        password="p@ss")
    conn = db.get_connection(cid)
    assert conn.has_password is True
    assert conn.is_default is True
    assert db.get_connection_password(cid) == "p@ss"
    assert db.get_default_connection().id == cid

    # updating other fields must NOT wipe the stored password
    conn.usr = "Admin"
    db.update_connection(conn)
    assert db.get_connection(cid).usr == "Admin"
    assert db.get_connection_password(cid) == "p@ss"


def test_cell_defaults_and_json_round_trip(db):
    cab = db.add_cabinet(Cabinet(name="c"))
    conn = db.add_connection(Connection1C(name="b"))
    warehouses = ["Квант (Новые) 3 этаж", "Товары по партиям"]
    cell_id = db.add_cell(Cell(name="Cell A", connection_id=conn, cabinet_id=cab,
                               code_templ=330017019, price_type="ZZap",
                               warehouses=warehouses))
    cell = db.get_cell(cell_id)
    assert cell.warehouses == warehouses
    assert cell.columns == {"producer": 1, "number": 2, "name": 3,
                            "quantity": 4, "price": 5}
    # safety defaults
    assert cell.staging_mode is True
    assert cell.enabled is False
    assert cell.include_header is False

    cell.enabled = True
    cell.staging_mode = False
    db.update_cell(cell)
    reloaded = db.get_cell(cell_id)
    assert reloaded.enabled is True
    assert reloaded.staging_mode is False
    assert len(db.list_cells(enabled_only=True)) == 1


def test_settings_typed_accessors(db):
    db.set_setting("interval_hours", "5")
    assert db.get_int("interval_hours") == 5
    db.set_bool("staging_mode", True)
    assert db.get_bool("staging_mode") is True
    assert db.get_setting("missing", "default") == "default"
    # upsert overwrites
    db.set_setting("interval_hours", "8")
    assert db.get_int("interval_hours") == 8


def test_run_history_lifecycle(db):
    cab = db.add_cabinet(Cabinet(name="c"))
    cell_id = db.add_cell(Cell(name="C", cabinet_id=cab, code_templ=1))
    rid = db.add_run(RunHistory(cell_id=cell_id, started_at="2026-06-23T10:00:00",
                                status="RUNNING"))
    db.finish_run(rid, finished_at="2026-06-23T10:00:05", status="OK", rows_sent=100)
    runs = db.list_runs(cell_id=cell_id)
    assert len(runs) == 1
    assert runs[0].status == "OK"
    assert runs[0].rows_sent == 100


def test_fk_cascade_deletes_run_history_with_cell(db):
    cab = db.add_cabinet(Cabinet(name="c"))
    cell_id = db.add_cell(Cell(name="C", cabinet_id=cab, code_templ=1))
    db.add_run(RunHistory(cell_id=cell_id, started_at="t", status="OK"))
    db.delete_cell(cell_id)
    assert db.list_runs() == []   # cascade removed the history row


def test_wal_mode_enabled_for_file_db(db):
    # Thread-affinity hardening: per-thread connections coexist via WAL + busy_timeout.
    mode = db._conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"


def test_import_exclusion_list_normalizes_and_dedupes(db):
    # Mixed input: lower/upper dup, blanks, None, a numeric article (Excel float/int).
    lid = db.import_exclusion_list(["a-1", "  A-1 ", "b-2", "", None, 12345], name="Excel")
    lst = db.get_exclusion_list(lid)
    assert lst.name == "Excel"
    assert lst.articles.splitlines() == ["A-1", "B-2", "12345"]


def test_import_exclusion_list_replace_and_append(db):
    lid = db.import_exclusion_list(["a-1"], name="L")
    # replace (append=False)
    db.import_exclusion_list(["c-3"], list_id=lid)
    assert db.get_exclusion_list(lid).articles.splitlines() == ["C-3"]
    # append: keep existing, add only genuinely-new (case-insensitive dedupe)
    db.import_exclusion_list(["c-3", "d-4"], list_id=lid, append=True)
    assert db.get_exclusion_list(lid).articles.splitlines() == ["C-3", "D-4"]


def test_import_exclusion_list_append_preserves_manual_comments(db):
    lid = db.add_exclusion_list("L", "# мои исключения\nA-1")
    db.import_exclusion_list(["b-2"], list_id=lid, append=True)
    assert db.get_exclusion_list(lid).articles.splitlines() == ["# мои исключения", "A-1", "B-2"]


def test_assign_exclusion_list_to_cell(db):
    cab = db.add_cabinet(Cabinet(name="c"))
    cell_id = db.add_cell(Cell(name="C", cabinet_id=cab, code_templ=1))
    lid = db.import_exclusion_list(["x-1"], name="L")
    db.assign_exclusion_list_to_cell(cell_id, lid)
    assert db.get_cell(cell_id).exclusion_list_id == lid
    db.assign_exclusion_list_to_cell(cell_id, None)   # clear
    assert db.get_cell(cell_id).exclusion_list_id is None
