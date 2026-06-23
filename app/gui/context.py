"""Shared application context for the GUI.

Holds the DB path + cipher + work dir, hands out a UI-thread ``Database`` for fast
local CRUD from event handlers, and makes fresh ``Database`` handles for worker
threads (a sqlite3 connection is bound to its creating thread — see app.db.dal).
Also caches the last 1C discovery (warehouses / price types) so the cell editor
does not re-query 1C every time it opens.

Construct one AppContext for the application's lifetime.
"""
from __future__ import annotations

from pathlib import Path

from .. import paths
from ..db.dal import Database
from ..security.secrets import Cipher
from ..services.connection import Discovery


class AppContext:
    def __init__(self, db_path: str | Path | None = None, *,
                 cipher: Cipher | None = None,
                 work_dir: str | Path | None = None) -> None:
        self.db_path = str(db_path or paths.db_path())
        self.cipher = cipher
        self.work_dir = Path(work_dir or paths.work_dir())
        self.work_dir.mkdir(parents=True, exist_ok=True)
        # Last live discovery from 1C (populated by the connection/cell screens).
        self.discovery: Discovery | None = None
        # UI-thread Database — only touch this from the Qt main thread.
        self.db = self.new_db()

    def new_db(self) -> Database:
        """A fresh Database bound to the CURRENT thread — use inside worker jobs."""
        return Database(self.db_path, self.cipher)

    def close(self) -> None:
        try:
            self.db.close()
        except Exception:  # noqa: BLE001 - best-effort cleanup on shutdown
            pass
