-- ZZap Sync app — SQLite schema (version 3).
-- Applied by app.db.dal.Database when PRAGMA user_version = 0, then
-- user_version := SCHEMA_VERSION. Older databases are upgraded incrementally
-- (see dal._migrate). Data model per ROADMAP.md §4. Secrets are stored as DPAPI
-- BLOBs, never plaintext.

-- A 1C connection target + credentials. `source` picks the read path:
--   'com'   — external COM connection (kind = 'server'|'file'; srvr/ref/file_path/progid).
--   'odata' — 1C standard OData over HTTP (odata_base_url + the odata_*_query fields);
--             usr/password_enc are reused as the HTTP Basic credentials.
-- The COM fields are ignored when source='odata' and vice-versa. `kind` keeps its
-- 'server'|'file' CHECK; an OData row leaves it at the 'server' default (unused).
CREATE TABLE IF NOT EXISTS connection_1c (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT    NOT NULL DEFAULT '',
    kind         TEXT    NOT NULL DEFAULT 'server' CHECK (kind IN ('server', 'file')),
    srvr         TEXT    NOT NULL DEFAULT '',   -- e.g. Serv1C  (server base)
    ref          TEXT    NOT NULL DEFAULT '',   -- e.g. ut2025  (server base)
    file_path    TEXT    NOT NULL DEFAULT '',   -- file base path (kind='file')
    progid       TEXT    NOT NULL DEFAULT 'V83.COMConnector',
    usr          TEXT    NOT NULL DEFAULT '',   -- 1C user (COM: External-connection right; OData: HTTP user)
    password_enc BLOB,                          -- DPAPI-encrypted; NULL if unset
    is_default   INTEGER NOT NULL DEFAULT 0,
    source       TEXT    NOT NULL DEFAULT 'com',    -- 'com' | 'odata'
    odata_base_url           TEXT NOT NULL DEFAULT '',  -- e.g. http://host/base/odata/standard.odata
    odata_nomenclature_query TEXT NOT NULL DEFAULT '',  -- entity set + $select/$filter for goods
    odata_prices_query       TEXT NOT NULL DEFAULT '',  -- optional: prices slice
    odata_stock_query        TEXT NOT NULL DEFAULT '',  -- optional: stock balances
    odata_producers_query    TEXT NOT NULL DEFAULT '',  -- optional: producer names
    odata_verify_ssl         INTEGER NOT NULL DEFAULT 1  -- 0 = skip TLS cert check (self-signed over VPN)
);

-- A ZZap cabinet/account = one API key. Cells reference a cabinet.
CREATE TABLE IF NOT EXISTS cabinet (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    api_key_enc BLOB,                           -- DPAPI-encrypted; NULL if unset
    api_url     TEXT    NOT NULL DEFAULT 'https://b52-api.zzap.pro/api/client/v1/price1c/upload'
);

-- An SMTP mailbox (schema v3) — the account the user "logs into" so the app can
-- e-mail the built price list from that address. Cells with target='email'
-- reference an account. The SMTP password is DPAPI-encrypted like other secrets.
CREATE TABLE IF NOT EXISTS email_account (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT    NOT NULL,
    smtp_host    TEXT    NOT NULL DEFAULT '',   -- e.g. smtp.mail.ru
    smtp_port    INTEGER NOT NULL DEFAULT 465,
    security     TEXT    NOT NULL DEFAULT 'ssl' CHECK (security IN ('ssl','starttls','none')),
    login        TEXT    NOT NULL DEFAULT '',   -- the mailbox address / SMTP login
    from_addr    TEXT    NOT NULL DEFAULT '',   -- optional explicit From; '' = login
    password_enc BLOB                           -- DPAPI-encrypted; NULL if unset
);

-- A reusable list of article numbers to exclude from uploads (the exclude.txt mechanism).
CREATE TABLE IF NOT EXISTS exclusion_list (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    name     TEXT NOT NULL,
    articles TEXT NOT NULL DEFAULT ''           -- one article per line; '#' = comment
);

-- An upload job: (warehouse(s) + price type) from 1C -> a delivery target.
-- target='zzap'  -> cabinet_id + code_templ (upload to a ZZap template);
-- target='email' -> email_account_id + email_to (XLSX e-mailed as an attachment).
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
    target            TEXT    NOT NULL DEFAULT 'zzap', -- 'zzap' | 'email' (schema v3)
    email_account_id  INTEGER REFERENCES email_account(id) ON DELETE SET NULL,
    email_to          TEXT    NOT NULL DEFAULT '',     -- recipients (comma/space separated)
    email_subject     TEXT    NOT NULL DEFAULT ''      -- '' = default subject with date
);

-- Key/value app settings (interval_hours, autostart, watchdog_enabled, ...).
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
