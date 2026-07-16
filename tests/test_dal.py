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
    assert version == 3


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


def test_odata_connection_round_trip(db):
    cid = db.add_connection(
        Connection1C(name="od", source="odata",
                     odata_base_url="http://host/base/odata/standard.odata",
                     odata_nomenclature_query="Catalog_Номенклатура?$select=Ref_Key",
                     odata_prices_query="InformationRegister_Цены_SliceLast",
                     odata_stock_query="AccumulationRegister_Товары_Balance",
                     odata_producers_query="Catalog_Производители",
                     usr="webuser", is_default=True),
        password="webpass")
    conn = db.get_connection(cid)
    assert conn.source == "odata"
    assert conn.odata_base_url.endswith("standard.odata")
    assert conn.odata_nomenclature_query.startswith("Catalog_Номенклатура")
    assert conn.odata_prices_query and conn.odata_stock_query and conn.odata_producers_query
    assert conn.usr == "webuser"
    assert conn.odata_verify_ssl is True          # secure default
    assert db.get_connection_password(cid) == "webpass"

    # editing OData fields (incl. disabling TLS check) without touching the password
    conn.odata_base_url = "http://host2/base/odata/standard.odata"
    conn.odata_verify_ssl = False
    db.update_connection(conn)
    reread = db.get_connection(cid)
    assert reread.odata_base_url.startswith("http://host2")
    assert reread.odata_verify_ssl is False
    assert db.get_connection_password(cid) == "webpass"


def test_migrate_v1_db_adds_odata_columns_and_keeps_data(tmp_path):
    # A database created by the pre-OData app (schema v1): build it by hand, then let
    # Database() upgrade it. The upgrade must be additive — the existing connection and
    # its (encrypted) password survive, and OData columns appear defaulted.
    import sqlite3

    path = tmp_path / "legacy.db"
    raw = sqlite3.connect(path)
    raw.executescript(
        """
        CREATE TABLE connection_1c (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL DEFAULT '',
            kind TEXT NOT NULL DEFAULT 'server' CHECK (kind IN ('server','file')),
            srvr TEXT NOT NULL DEFAULT '', ref TEXT NOT NULL DEFAULT '',
            file_path TEXT NOT NULL DEFAULT '',
            progid TEXT NOT NULL DEFAULT 'V83.COMConnector',
            usr TEXT NOT NULL DEFAULT '', password_enc BLOB,
            is_default INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE cell (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 0,
            connection_id INTEGER,
            cabinet_id INTEGER,
            code_templ INTEGER NOT NULL,
            price_type TEXT NOT NULL DEFAULT '',
            warehouses TEXT NOT NULL DEFAULT '[]',
            exclusion_list_id INTEGER,
            include_header INTEGER NOT NULL DEFAULT 0,
            columns TEXT NOT NULL DEFAULT '{"producer":1,"number":2,"name":3,"quantity":4,"price":5}'
        );
        """)
    raw.execute(
        "INSERT INTO connection_1c (name, kind, srvr, ref, usr, password_enc, is_default)"
        " VALUES ('legacy','server','Serv1C','ut2025','Натали', ?, 1)",
        (FakeCipher().encrypt("p@ss"),))
    raw.execute(
        "INSERT INTO cell (name, enabled, code_templ, price_type, warehouses)"
        " VALUES ('old cell', 1, 330017019, 'ZZap', '[\"W\"]')")
    raw.execute("PRAGMA user_version = 1")
    raw.commit()
    raw.close()

    database = Database(path, cipher=FakeCipher())
    try:
        assert database._conn.execute("PRAGMA user_version").fetchone()[0] == 3
        cols = {r["name"] for r in
                database._conn.execute("PRAGMA table_info(connection_1c)")}
        assert {"source", "odata_base_url", "odata_nomenclature_query",
                "odata_prices_query", "odata_stock_query",
                "odata_producers_query"} <= cols
        conn = database.get_default_connection()
        assert conn is not None and conn.name == "legacy"
        assert conn.source == "com"                      # defaulted for old rows
        assert database.get_connection_password(conn.id) == "p@ss"
        # v3: the old cell survives with its behaviour unchanged (target='zzap')
        cells = database.list_cells()
        assert len(cells) == 1 and cells[0].name == "old cell"
        assert cells[0].target == "zzap"
        assert cells[0].email_account_id is None and cells[0].email_to == ""
        assert database.list_email_accounts() == []      # v3 table exists and is empty
    finally:
        database.close()


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
    assert cell.enabled is False
    assert cell.include_header is False

    cell.enabled = True
    db.update_cell(cell)
    reloaded = db.get_cell(cell_id)
    assert reloaded.enabled is True
    assert len(db.list_cells(enabled_only=True)) == 1


def test_finalize_orphan_runs_marks_interrupted(db):
    from app.db.models import RunHistory
    cab = db.add_cabinet(Cabinet(name="c"))
    conn = db.add_connection(Connection1C(name="b"))
    cid = db.add_cell(Cell(name="C", connection_id=conn, cabinet_id=cab,
                           code_templ=1, price_type="ZZap", warehouses=["W"]))
    ok = db.add_run(RunHistory(cell_id=cid, started_at="2026-06-24T10:00:00",
                               finished_at="2026-06-24T10:01:00", status="OK"))
    orphan = db.add_run(RunHistory(cell_id=cid, started_at="2026-06-24T11:00:00",
                                   status="RUNNING"))  # crash left it RUNNING

    fixed = db.finalize_orphan_runs()
    assert fixed == 1
    runs = {r.id: r for r in db.list_runs(cell_id=cid)}
    assert runs[ok].status == "OK"                     # completed run untouched
    assert runs[orphan].status == "ERROR"              # orphan marked interrupted
    assert runs[orphan].finished_at == "2026-06-24T11:00:00"
    assert "Прервано" in (runs[orphan].message or "")
    assert db.finalize_orphan_runs() == 0              # idempotent


def test_settings_typed_accessors(db):
    db.set_setting("interval_hours", "5")
    assert db.get_int("interval_hours") == 5
    db.set_bool("autostart", True)
    assert db.get_bool("autostart") is True
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
