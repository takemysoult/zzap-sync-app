"""DAL: email_account CRUD (пароль шифруется), ячейки с target='email',
миграция v2 -> v3 существующей базы.
"""
import sqlite3

import pytest

from app.db.dal import Database
from app.db.models import Cell, EmailAccount
from conftest import FakeCipher


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "test.db", cipher=FakeCipher())
    try:
        yield database
    finally:
        database.close()


def test_email_account_crud_password_encrypted_at_rest(db):
    acc_id = db.add_email_account(
        EmailAccount(name="Рабочая", smtp_host="smtp.mail.ru", smtp_port=465,
                     security="ssl", login="me@mail.ru"),
        password="app-secret")
    acc = db.get_email_account(acc_id)
    assert acc.name == "Рабочая"
    assert acc.smtp_host == "smtp.mail.ru" and acc.smtp_port == 465
    assert acc.security == "ssl" and acc.login == "me@mail.ru"
    assert acc.has_password is True
    assert db.get_email_account_password(acc_id) == "app-secret"
    # stored blob must NOT contain the plaintext password
    raw = db._conn.execute(
        "SELECT password_enc FROM email_account WHERE id=?", (acc_id,)).fetchone()[0]
    assert b"app-secret" not in bytes(raw)

    # partial update keeps the stored password
    acc.smtp_port = 587
    acc.security = "starttls"
    db.update_email_account(acc)
    reread = db.get_email_account(acc_id)
    assert reread.smtp_port == 587 and reread.security == "starttls"
    assert db.get_email_account_password(acc_id) == "app-secret"

    # explicit password update replaces it
    db.update_email_account(acc, password="new-secret", update_password=True)
    assert db.get_email_account_password(acc_id) == "new-secret"

    db.delete_email_account(acc_id)
    assert db.get_email_account(acc_id) is None
    assert db.list_email_accounts() == []


def test_email_cell_round_trip(db):
    acc_id = db.add_email_account(
        EmailAccount(name="A", smtp_host="h", login="a@b.c"), password="p")
    cell_id = db.add_cell(Cell(
        name="Почтовая", enabled=True, code_templ=0, target="email",
        email_account_id=acc_id, email_to="client@example.com, boss@example.com",
        email_subject="Прайс"))
    cell = db.get_cell(cell_id)
    assert cell.target == "email"
    assert cell.email_account_id == acc_id
    assert cell.email_to == "client@example.com, boss@example.com"
    assert cell.email_subject == "Прайс"
    assert cell.cabinet_id is None

    cell.email_to = "only@example.com"
    cell.target = "zzap"
    db.update_cell(cell)
    reread = db.get_cell(cell_id)
    assert reread.target == "zzap" and reread.email_to == "only@example.com"


def test_deleting_account_nulls_cell_reference(db):
    acc_id = db.add_email_account(
        EmailAccount(name="A", smtp_host="h", login="a@b.c"), password="p")
    cell_id = db.add_cell(Cell(name="c", code_templ=0, target="email",
                               email_account_id=acc_id, email_to="x@y.z"))
    db.delete_email_account(acc_id)
    assert db.get_cell(cell_id).email_account_id is None   # ON DELETE SET NULL


def test_migrate_v2_db_adds_email_support_and_keeps_data(tmp_path):
    """База, созданная приложением до v3 (schema v2): апгрейд додаёт таблицу
    email_account и колонки target/email_* к cell, не трогая данные."""
    path = tmp_path / "v2.db"
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
            is_default INTEGER NOT NULL DEFAULT 0,
            source TEXT NOT NULL DEFAULT 'com',
            odata_base_url TEXT NOT NULL DEFAULT '',
            odata_nomenclature_query TEXT NOT NULL DEFAULT '',
            odata_prices_query TEXT NOT NULL DEFAULT '',
            odata_stock_query TEXT NOT NULL DEFAULT '',
            odata_producers_query TEXT NOT NULL DEFAULT '',
            odata_verify_ssl INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE cabinet (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL, api_key_enc BLOB,
            api_url TEXT NOT NULL DEFAULT ''
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
    raw.execute("INSERT INTO cabinet (name, api_url) VALUES ('Cab', 'u')")
    raw.execute(
        "INSERT INTO cell (name, enabled, cabinet_id, code_templ, price_type,"
        " warehouses) VALUES ('zzap cell', 1, 1, 330017019, 'ZZap', '[\"W\"]')")
    raw.execute("PRAGMA user_version = 2")
    raw.commit()
    raw.close()

    database = Database(path, cipher=FakeCipher())
    try:
        assert database._conn.execute("PRAGMA user_version").fetchone()[0] == 3
        # migrated cell schema matches the fresh one (columns present, defaulted)
        cell = database.list_cells()[0]
        assert cell.name == "zzap cell"
        assert cell.target == "zzap"                     # existing behaviour kept
        assert cell.email_account_id is None
        assert cell.email_to == "" and cell.email_subject == ""
        # email_account is usable right away
        acc_id = database.add_email_account(
            EmailAccount(name="A", smtp_host="h", login="a@b.c"), password="s")
        assert database.get_email_account_password(acc_id) == "s"

        # fresh-schema and migrated-schema cell columns must be identical
        migrated_cols = {r["name"] for r in
                         database._conn.execute("PRAGMA table_info(cell)")}
        migrated_acc_cols = {r["name"] for r in
                             database._conn.execute("PRAGMA table_info(email_account)")}
    finally:
        database.close()

    fresh = Database(tmp_path / "fresh.db", cipher=FakeCipher())
    try:
        fresh_cols = {r["name"] for r in
                      fresh._conn.execute("PRAGMA table_info(cell)")}
        fresh_acc_cols = {r["name"] for r in
                          fresh._conn.execute("PRAGMA table_info(email_account)")}
    finally:
        fresh.close()
    assert migrated_cols == fresh_cols
    assert migrated_acc_cols == fresh_acc_cols
    assert {"id", "name", "smtp_host", "smtp_port", "security", "login",
            "from_addr", "password_enc"} == fresh_acc_cols
