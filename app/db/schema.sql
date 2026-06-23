-- ZZap Sync app — SQLite schema (version 1).
-- Applied by app.db.dal.Database when PRAGMA user_version = 0, then user_version := 1.
-- Data model per ROADMAP.md §4. Secrets are stored as DPAPI BLOBs, never plaintext.

-- A 1C connection target (server base or file base) + external-connection credentials.
CREATE TABLE IF NOT EXISTS connection_1c (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT    NOT NULL DEFAULT '',
    kind         TEXT    NOT NULL DEFAULT 'server' CHECK (kind IN ('server', 'file')),
    srvr         TEXT    NOT NULL DEFAULT '',   -- e.g. Serv1C  (server base)
    ref          TEXT    NOT NULL DEFAULT '',   -- e.g. ut2025  (server base)
    file_path    TEXT    NOT NULL DEFAULT '',   -- file base path (kind='file')
    progid       TEXT    NOT NULL DEFAULT 'V83.COMConnector',
    usr          TEXT    NOT NULL DEFAULT '',   -- 1C user (needs External-connection right)
    password_enc BLOB,                          -- DPAPI-encrypted; NULL if unset
    is_default   INTEGER NOT NULL DEFAULT 0
);

-- A ZZap cabinet/account = one API key. Cells reference a cabinet.
CREATE TABLE IF NOT EXISTS cabinet (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    api_key_enc BLOB,                           -- DPAPI-encrypted; NULL if unset
    api_url     TEXT    NOT NULL DEFAULT 'https://b52-api.zzap.pro/api/client/v1/price1c/upload'
);

-- A reusable list of article numbers to exclude from uploads (the exclude.txt mechanism).
CREATE TABLE IF NOT EXISTS exclusion_list (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    name     TEXT NOT NULL,
    articles TEXT NOT NULL DEFAULT ''           -- one article per line; '#' = comment
);

-- An upload job: (warehouse(s) + price type) from 1C -> a ZZap code_templ.
CREATE TABLE IF NOT EXISTS cell (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT    NOT NULL,
    enabled           INTEGER NOT NULL DEFAULT 0,
    connection_id     INTEGER REFERENCES connection_1c(id) ON DELETE SET NULL,
    cabinet_id        INTEGER REFERENCES cabinet(id)       ON DELETE SET NULL,
    code_templ        INTEGER NOT NULL,
    price_type        TEXT    NOT NULL DEFAULT '',
    warehouses        TEXT    NOT NULL DEFAULT '[]',  -- JSON array of warehouse names
    exclusion_list_id INTEGER REFERENCES exclusion_list(id) ON DELETE SET NULL,
    include_header    INTEGER NOT NULL DEFAULT 0,      -- ZZap templates expect NO header
    columns           TEXT    NOT NULL DEFAULT '{"producer":1,"number":2,"name":3,"quantity":4,"price":5}',
    -- Per-cell safety gate: a NEW cell is staging by default (build file, do NOT POST).
    -- Real uploads require the user to flip this off explicitly (PROMPT.md "Safety first").
    staging_mode      INTEGER NOT NULL DEFAULT 1
);

-- Key/value app settings (interval_hours, staging_mode global kill-switch, autostart, ...).
CREATE TABLE IF NOT EXISTS setting (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- One row per cell run (observability; PROMPT.md "idempotent & observable").
CREATE TABLE IF NOT EXISTS run_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    cell_id     INTEGER REFERENCES cell(id) ON DELETE CASCADE,
    started_at  TEXT    NOT NULL,
    finished_at TEXT,
    status      TEXT    NOT NULL,               -- OK | FAIL | STAGED | ERROR | RESEND_OK ...
    rows_sent   INTEGER,
    rows_note   TEXT,
    message     TEXT
);

CREATE INDEX IF NOT EXISTS idx_run_history_cell ON run_history (cell_id, started_at);
