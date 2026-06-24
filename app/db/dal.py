"""SQLite data-access layer.

One `Database` wraps a connection and exposes typed CRUD for the app's entities.
Secrets (1C password, ZZap API key) are encrypted on write via the injected
`Cipher` (DPAPI by default) and only decrypted through dedicated accessors; ordinary
reads return models with a `has_*` flag instead of the secret.

Threading note: a sqlite3 connection is bound to its creating thread. The service
layer (Phase 2+) opens a `Database` per worker thread rather than sharing one (the
scheduler runs each cell on a worker thread; every worker opens its own Database).
To make those independent connections coexist on one file we enable WAL +
busy_timeout: WAL lets a reader and the writer proceed concurrently, and the
busy_timeout makes a momentary write-lock collision wait-and-retry instead of
raising "database is locked". Migrations run on the first open (main thread at
startup) before any worker connects.
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from pathlib import Path

from engine.transform import normalize_articles, parse_exclusions

from ..security.secrets import Cipher, DpapiCipher
from .models import Cabinet, Cell, Connection1C, ExclusionList, RunHistory

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")
SCHEMA_VERSION = 1

_DEFAULT_API_URL = "https://b52-api.zzap.pro/api/client/v1/price1c/upload"
_DEFAULT_COLUMNS = {"producer": 1, "number": 2, "name": 3, "quantity": 4, "price": 5}


class Database:
    def __init__(self, path: str | Path, cipher: Cipher | None = None) -> None:
        self.path = str(path)
        self._cipher = cipher or DpapiCipher()
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        # FK enforcement is per-connection and must be set outside a transaction.
        self._conn.execute("PRAGMA foreign_keys = ON")
        # Wait (don't raise) up to 5s if another worker thread holds the write lock.
        self._conn.execute("PRAGMA busy_timeout = 5000")
        # WAL allows concurrent reader+writer across per-thread connections. It is a
        # persistent on-disk mode (no-op / unsupported for :memory:), so only file DBs.
        if self.path != ":memory:":
            self._conn.execute("PRAGMA journal_mode = WAL")
        self._migrate()

    # --- lifecycle --------------------------------------------------------
    def _migrate(self) -> None:
        version = self._conn.execute("PRAGMA user_version").fetchone()[0]
        if version == 0:
            self._conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
            self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            self._conn.commit()
        # Future schema bumps: elif version < SCHEMA_VERSION: apply incremental steps.

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # --- secret helpers ---------------------------------------------------
    def _enc(self, plaintext: str | None) -> bytes | None:
        if plaintext is None or plaintext == "":
            return None
        return self._cipher.encrypt(plaintext)

    def _dec(self, blob: bytes | None) -> str | None:
        if blob is None:
            return None
        return self._cipher.decrypt(bytes(blob))

    # ==================================================================
    # connection_1c
    # ==================================================================
    def add_connection(self, conn: Connection1C, password: str | None = None) -> int:
        cur = self._conn.execute(
            """INSERT INTO connection_1c
               (name, kind, srvr, ref, file_path, progid, usr, password_enc, is_default)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (conn.name, conn.kind, conn.srvr, conn.ref, conn.file_path, conn.progid,
             conn.usr, self._enc(password), int(conn.is_default)),
        )
        self._conn.commit()
        new_id = int(cur.lastrowid)
        if conn.is_default:
            self.set_default_connection(new_id)
        return new_id

    def get_connection(self, conn_id: int) -> Connection1C | None:
        row = self._conn.execute(
            "SELECT * FROM connection_1c WHERE id = ?", (conn_id,)).fetchone()
        return _row_to_connection(row) if row else None

    def list_connections(self) -> list[Connection1C]:
        rows = self._conn.execute(
            "SELECT * FROM connection_1c ORDER BY id").fetchall()
        return [_row_to_connection(r) for r in rows]

    def get_default_connection(self) -> Connection1C | None:
        row = self._conn.execute(
            "SELECT * FROM connection_1c WHERE is_default = 1 ORDER BY id LIMIT 1").fetchone()
        return _row_to_connection(row) if row else None

    def update_connection(self, conn: Connection1C,
                          password: str | None = None,
                          update_password: bool = False) -> None:
        """Update a connection. The password is only touched when update_password=True
        (so callers can save other fields without clearing the stored secret)."""
        if conn.id is None:
            raise ValueError("update_connection requires conn.id")
        if update_password:
            self._conn.execute(
                """UPDATE connection_1c SET name=?, kind=?, srvr=?, ref=?, file_path=?,
                   progid=?, usr=?, password_enc=?, is_default=? WHERE id=?""",
                (conn.name, conn.kind, conn.srvr, conn.ref, conn.file_path, conn.progid,
                 conn.usr, self._enc(password), int(conn.is_default), conn.id),
            )
        else:
            self._conn.execute(
                """UPDATE connection_1c SET name=?, kind=?, srvr=?, ref=?, file_path=?,
                   progid=?, usr=?, is_default=? WHERE id=?""",
                (conn.name, conn.kind, conn.srvr, conn.ref, conn.file_path, conn.progid,
                 conn.usr, int(conn.is_default), conn.id),
            )
        self._conn.commit()
        if conn.is_default:
            self.set_default_connection(conn.id)

    def set_default_connection(self, conn_id: int) -> None:
        self._conn.execute("UPDATE connection_1c SET is_default = 0")
        self._conn.execute(
            "UPDATE connection_1c SET is_default = 1 WHERE id = ?", (conn_id,))
        self._conn.commit()

    def delete_connection(self, conn_id: int) -> None:
        self._conn.execute("DELETE FROM connection_1c WHERE id = ?", (conn_id,))
        self._conn.commit()

    def get_connection_password(self, conn_id: int) -> str | None:
        row = self._conn.execute(
            "SELECT password_enc FROM connection_1c WHERE id = ?", (conn_id,)).fetchone()
        return self._dec(row["password_enc"]) if row else None

    # ==================================================================
    # cabinet
    # ==================================================================
    def add_cabinet(self, cabinet: Cabinet, api_key: str | None = None) -> int:
        cur = self._conn.execute(
            "INSERT INTO cabinet (name, api_key_enc, api_url) VALUES (?,?,?)",
            (cabinet.name, self._enc(api_key), cabinet.api_url or _DEFAULT_API_URL),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def get_cabinet(self, cabinet_id: int) -> Cabinet | None:
        row = self._conn.execute(
            "SELECT * FROM cabinet WHERE id = ?", (cabinet_id,)).fetchone()
        return _row_to_cabinet(row) if row else None

    def list_cabinets(self) -> list[Cabinet]:
        rows = self._conn.execute("SELECT * FROM cabinet ORDER BY id").fetchall()
        return [_row_to_cabinet(r) for r in rows]

    def update_cabinet(self, cabinet: Cabinet, api_key: str | None = None,
                       update_api_key: bool = False) -> None:
        if cabinet.id is None:
            raise ValueError("update_cabinet requires cabinet.id")
        if update_api_key:
            self._conn.execute(
                "UPDATE cabinet SET name=?, api_key_enc=?, api_url=? WHERE id=?",
                (cabinet.name, self._enc(api_key), cabinet.api_url or _DEFAULT_API_URL,
                 cabinet.id),
            )
        else:
            self._conn.execute(
                "UPDATE cabinet SET name=?, api_url=? WHERE id=?",
                (cabinet.name, cabinet.api_url or _DEFAULT_API_URL, cabinet.id),
            )
        self._conn.commit()

    def delete_cabinet(self, cabinet_id: int) -> None:
        self._conn.execute("DELETE FROM cabinet WHERE id = ?", (cabinet_id,))
        self._conn.commit()

    def get_cabinet_api_key(self, cabinet_id: int) -> str | None:
        row = self._conn.execute(
            "SELECT api_key_enc FROM cabinet WHERE id = ?", (cabinet_id,)).fetchone()
        return self._dec(row["api_key_enc"]) if row else None

    # ==================================================================
    # exclusion_list
    # ==================================================================
    def add_exclusion_list(self, name: str, articles: str = "") -> int:
        cur = self._conn.execute(
            "INSERT INTO exclusion_list (name, articles) VALUES (?,?)", (name, articles))
        self._conn.commit()
        return int(cur.lastrowid)

    def get_exclusion_list(self, list_id: int) -> ExclusionList | None:
        row = self._conn.execute(
            "SELECT * FROM exclusion_list WHERE id = ?", (list_id,)).fetchone()
        return ExclusionList(id=row["id"], name=row["name"], articles=row["articles"]) \
            if row else None

    def list_exclusion_lists(self) -> list[ExclusionList]:
        rows = self._conn.execute("SELECT * FROM exclusion_list ORDER BY id").fetchall()
        return [ExclusionList(id=r["id"], name=r["name"], articles=r["articles"])
                for r in rows]

    def update_exclusion_list(self, lst: ExclusionList) -> None:
        if lst.id is None:
            raise ValueError("update_exclusion_list requires id")
        self._conn.execute(
            "UPDATE exclusion_list SET name=?, articles=? WHERE id=?",
            (lst.name, lst.articles, lst.id))
        self._conn.commit()

    def delete_exclusion_list(self, list_id: int) -> None:
        self._conn.execute("DELETE FROM exclusion_list WHERE id = ?", (list_id,))
        self._conn.commit()

    def import_exclusion_list(self, articles: Iterable[object], *,
                              name: str | None = None, list_id: int | None = None,
                              append: bool = False) -> int:
        """Create or update an `exclusion_list` from imported article strings.

        `articles` is a raw iterable (e.g. the output of
        `engine.exclusions.read_exclusion_articles`). Values are normalized once
        here (trim+upper, deduped, blanks dropped) via the engine's shared
        `normalize_articles`, then stored one-per-line. `apply_exclusions`
        re-normalizes at run time, so storage and matching stay consistent.

        - `list_id is None`           -> create a NEW list (returns its id).
        - `list_id`, `append=False`   -> replace that list's articles.
        - `list_id`, `append=True`    -> merge with existing (existing text — incl.
          any manual `#` comments — is preserved; only genuinely new articles are
          appended).

        Returns the list id.
        """
        new_articles = normalize_articles(articles)
        if list_id is None:
            return self.add_exclusion_list(name or "Импорт из Excel",
                                           "\n".join(new_articles))
        existing = self.get_exclusion_list(list_id)
        if existing is None:
            raise ValueError(f"Список исключений id={list_id} не найден.")
        if append:
            already = parse_exclusions(existing.articles)  # normalized set, comments skipped
            additions = [a for a in new_articles if a not in already]
            base = existing.articles.rstrip("\n")
            existing.articles = (
                base + ("\n" if base and additions else "") + "\n".join(additions)
                if additions else existing.articles
            )
        else:
            existing.articles = "\n".join(new_articles)
        if name is not None:
            existing.name = name
        self.update_exclusion_list(existing)
        return list_id

    def assign_exclusion_list_to_cell(self, cell_id: int, list_id: int | None) -> None:
        """Point a cell at an exclusion list (pass list_id=None to clear it)."""
        self._conn.execute(
            "UPDATE cell SET exclusion_list_id = ? WHERE id = ?", (list_id, cell_id))
        self._conn.commit()

    # ==================================================================
    # cell
    # ==================================================================
    def add_cell(self, cell: Cell) -> int:
        cur = self._conn.execute(
            """INSERT INTO cell
               (name, enabled, connection_id, cabinet_id, code_templ, price_type,
                warehouses, exclusion_list_id, include_header, columns)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (cell.name, int(cell.enabled), cell.connection_id, cell.cabinet_id,
             cell.code_templ, cell.price_type,
             json.dumps(cell.warehouses, ensure_ascii=False),
             cell.exclusion_list_id, int(cell.include_header),
             json.dumps(cell.columns)),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def get_cell(self, cell_id: int) -> Cell | None:
        row = self._conn.execute("SELECT * FROM cell WHERE id = ?", (cell_id,)).fetchone()
        return _row_to_cell(row) if row else None

    def list_cells(self, enabled_only: bool = False) -> list[Cell]:
        sql = "SELECT * FROM cell"
        if enabled_only:
            sql += " WHERE enabled = 1"
        sql += " ORDER BY id"
        return [_row_to_cell(r) for r in self._conn.execute(sql).fetchall()]

    def update_cell(self, cell: Cell) -> None:
        if cell.id is None:
            raise ValueError("update_cell requires cell.id")
        self._conn.execute(
            """UPDATE cell SET name=?, enabled=?, connection_id=?, cabinet_id=?,
               code_templ=?, price_type=?, warehouses=?, exclusion_list_id=?,
               include_header=?, columns=? WHERE id=?""",
            (cell.name, int(cell.enabled), cell.connection_id, cell.cabinet_id,
             cell.code_templ, cell.price_type,
             json.dumps(cell.warehouses, ensure_ascii=False),
             cell.exclusion_list_id, int(cell.include_header),
             json.dumps(cell.columns), cell.id),
        )
        self._conn.commit()

    def delete_cell(self, cell_id: int) -> None:
        self._conn.execute("DELETE FROM cell WHERE id = ?", (cell_id,))
        self._conn.commit()

    # ==================================================================
    # setting (key/value)
    # ==================================================================
    def get_setting(self, key: str, default: str | None = None) -> str | None:
        row = self._conn.execute(
            "SELECT value FROM setting WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO setting (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)))
        self._conn.commit()

    def get_int(self, key: str, default: int = 0) -> int:
        val = self.get_setting(key)
        try:
            return int(val) if val is not None else default
        except (TypeError, ValueError):
            return default

    def get_bool(self, key: str, default: bool = False) -> bool:
        val = self.get_setting(key)
        if val is None:
            return default
        return val.strip().lower() in ("1", "true", "yes", "on")

    def set_bool(self, key: str, value: bool) -> None:
        self.set_setting(key, "1" if value else "0")

    # ==================================================================
    # run_history
    # ==================================================================
    def add_run(self, run: RunHistory) -> int:
        cur = self._conn.execute(
            """INSERT INTO run_history
               (cell_id, started_at, finished_at, status, rows_sent, rows_note, message)
               VALUES (?,?,?,?,?,?,?)""",
            (run.cell_id, run.started_at, run.finished_at, run.status,
             run.rows_sent, run.rows_note, run.message),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def finish_run(self, run_id: int, finished_at: str, status: str,
                   rows_sent: int | None = None, rows_note: str | None = None,
                   message: str | None = None) -> None:
        self._conn.execute(
            """UPDATE run_history SET finished_at=?, status=?, rows_sent=?,
               rows_note=?, message=? WHERE id=?""",
            (finished_at, status, rows_sent, rows_note, message, run_id))
        self._conn.commit()

    def list_runs(self, cell_id: int | None = None, limit: int = 100) -> list[RunHistory]:
        if cell_id is None:
            rows = self._conn.execute(
                "SELECT * FROM run_history ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM run_history WHERE cell_id = ? ORDER BY id DESC LIMIT ?",
                (cell_id, limit)).fetchall()
        return [_row_to_run(r) for r in rows]


# ----------------------------------------------------------------------
# row -> dataclass mappers
# ----------------------------------------------------------------------
def _row_to_connection(row: sqlite3.Row) -> Connection1C:
    return Connection1C(
        id=row["id"], name=row["name"], kind=row["kind"], srvr=row["srvr"],
        ref=row["ref"], file_path=row["file_path"], progid=row["progid"],
        usr=row["usr"], has_password=row["password_enc"] is not None,
        is_default=bool(row["is_default"]),
    )


def _row_to_cabinet(row: sqlite3.Row) -> Cabinet:
    return Cabinet(
        id=row["id"], name=row["name"], api_url=row["api_url"],
        has_api_key=row["api_key_enc"] is not None,
    )


def _row_to_cell(row: sqlite3.Row) -> Cell:
    try:
        warehouses = json.loads(row["warehouses"]) or []
    except (TypeError, ValueError):
        warehouses = []
    try:
        columns = json.loads(row["columns"]) or dict(_DEFAULT_COLUMNS)
    except (TypeError, ValueError):
        columns = dict(_DEFAULT_COLUMNS)
    return Cell(
        id=row["id"], name=row["name"], enabled=bool(row["enabled"]),
        connection_id=row["connection_id"], cabinet_id=row["cabinet_id"],
        code_templ=row["code_templ"], price_type=row["price_type"],
        warehouses=warehouses, exclusion_list_id=row["exclusion_list_id"],
        include_header=bool(row["include_header"]), columns=columns,
    )


def _row_to_run(row: sqlite3.Row) -> RunHistory:
    return RunHistory(
        id=row["id"], cell_id=row["cell_id"], started_at=row["started_at"],
        finished_at=row["finished_at"], status=row["status"],
        rows_sent=row["rows_sent"], rows_note=row["rows_note"], message=row["message"],
    )
