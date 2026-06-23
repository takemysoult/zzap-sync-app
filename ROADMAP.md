# ZZap Sync App — Roadmap

A Windows desktop app that manages price-list uploads from 1C to ZZap. Replaces the
manual `config.ini` + Task Scheduler CLI with a GUI built around **upload cells**.

Read **PROJECT_MEMORY.md** first — it holds all verified facts (1C COM, ZZap API,
data model). This file is the build plan.

---

## 1. Product definition

### The "cell" (core concept)
A **cell** is one upload job. Fields:
- **Name** (label).
- **Cabinet** → which ZZap account = which **API key** (cells can belong to different cabinets).
- **`code_templ`** — ZZap template code (entered manually by the user).
- **Warehouse(s)** — chosen from a dropdown populated **live from 1C** (single or multi-select).
- **Price type** — chosen from a dropdown populated **live from 1C**.
- **Enabled** toggle, plus optional per-cell exclusions / column layout overrides.

At each scheduled tick the app runs every enabled cell:
`build 1C query (warehouses + price type) → fetch rows → apply exclusions →
build XLSX (5 cols) → upload to code_templ with the cabinet's API key`.

### Settings
- **Upload interval** (every N hours) — the primary requested setting.
- 1C connection (server/base or file path) + **login/password** (admin / external-connection).
- ZZap cabinets (name + API key each), stored encrypted.
- Global options: staging/"dry-run" mode (build but don't send), start-with-Windows,
  notifications.

### Live 1C data
After the user enters 1C credentials and the app connects via COM, it queries 1C to fill:
- **Warehouses** (`Справочник.Склады`).
- **Price types** (`Справочник.ВидыЦен`).
`code_templ` is NOT discoverable from 1C — it is typed by the user (and verified against the
ZZap cabinet manually; the app shows the per-template checklist from PROJECT_MEMORY §5).

---

## 2. Recommended tech stack (architect confirms/overrides)

- **Language:** Python 3.12, 64-bit (mandatory for 1C COM parity).
- **GUI:** **PySide6 (Qt)** — native desktop, strong tables/forms, system tray.
  - Alternative considered: local web UI (FastAPI + browser) — more moving parts; defer.
- **Scheduler:** **APScheduler** running inside a **system-tray background app**
  (misfire grace + coalescing for catch-up). Optionally also register a Windows Scheduled
  Task as a resiliency backstop.
- **Engine:** reuse/extract `zzapsync` from the old project into an `engine/` package
  (COM source, transform, zzap_client, delivery). Keep it UI-agnostic and unit-testable.
- **Config/data store:** **SQLite** (cells, cabinets, settings, run history) via a thin DAL.
  Small and queryable; JSON is acceptable for v0 but SQLite scales to history/logs.
- **Secrets:** **Windows DPAPI** (`win32crypt`) or `keyring` — never store 1C password or
  ZZap keys in plaintext.
- **Packaging:** **PyInstaller** → single signed-ish `.exe` + a simple installer; autostart
  via registry Run key or a per-user Scheduled Task.
- **Threading:** all COM work on a dedicated worker thread/process with its own
  `CoInitialize`/`CoUninitialize`; UI never blocks on 1C or network.

---

## 3. Architecture (layers)

```
┌────────────────────────────────────────────┐
│ GUI (PySide6)                               │  config + monitoring, no business logic
│  - Connection screen   - Cells table        │
│  - Settings            - Logs / status      │
├────────────────────────────────────────────┤
│ Application/service layer                   │  orchestrates use-cases
│  - CellRunner (run one cell)                │
│  - Scheduler (APScheduler) + catch-up       │
│  - ConnectionManager (1C COM, validate)     │
│  - Secrets (DPAPI)                           │
├────────────────────────────────────────────┤
│ Engine (reused zzapsync)                    │  pure, testable
│  - com source   - query builder             │
│  - transform/build_xlsx   - exclusions      │
│  - zzap_client (price1c/upload)             │
│  - delivery (journal/state/pending)         │
├────────────────────────────────────────────┤
│ Data store (SQLite) + Secrets (DPAPI)       │
└────────────────────────────────────────────┘
```

COM threading note: the engine runs in a worker; the GUI talks to it via signals/queues.

---

## 4. Data model (initial)

- **connection_1c**: id, kind(server|file), srvr, ref, file_path, progid, user, password(enc), is_default
- **cabinet**: id, name, api_key(enc), api_url(default price1c/upload)
- **cell**: id, name, enabled, connection_id, cabinet_id, code_templ, price_type,
  warehouses(json list), exclude_list_id?, include_header(false), columns(json, default 1..5)
- **setting**: interval_hours, staging_mode(bool), autostart(bool), ...
- **run_history**: id, cell_id, started_at, finished_at, status, rows_sent, rows_note, message
- **exclusion_list**: id, name, articles(text)   (the exclude.txt mechanism, per list)

---

## 5. Phased delivery

### Phase 0 — Foundations  ✅ DONE (2026-06-23, reviewed: SHIP)
- Repo scaffold, 64-bit venv (Python 3.12 x64), deps, pytest.
- Extracted `engine/` from old `zzapsync`; unit tests around transform/zzap_client
  (HTTP mocked via injectable session) and COM behind the `PriceSource` interface.
- SQLite schema + DAL (`app/db`) with `user_version` migration; DPAPI secrets helper
  (`app/security`) with a `Cipher` protocol so the DAL is testable without DPAPI.
- 28 tests pass without a live 1C/ZZap.
**Exit met:** engine builds the 5-col XLSX from sample rows; mocked upload sends the
exact `part_num=0/part_total=1` payload; secrets round-trip encrypted; FK + staging
defaults enforced.
**Deferred to Phase 2 (Reviewer notes, not blockers):**
- DAL thread-affinity: a sqlite3 connection is bound to its creating thread. CellRunner/
  scheduler must open a `Database` per worker thread (or `check_same_thread=False` + lock).
- Add an end-to-end test that a cabinet's stored `api_url` flows into `ZzapConfig` and
  `upload_price` POSTs to it (guards against a wrong-host upload).
- If DPAPI entropy is later added for defense-in-depth, the DAL must persist/derive it
  consistently or existing blobs become undecryptable.

### Phase 1 — 1C connection & discovery  🟡 CODE COMPLETE (2026-06-23, mocked-COM; reviewed: SHIP-WITH-FIXES → fixed)
- `app/services/connection.py:ConnectionManager` — connect via COM, `test_connection`
  (never raises; friendly RU errors via `describe_1c_error`), `discover` (warehouses +
  price types over ONE connection), `build_conn_string` (server/file), `to_com_config`.
- `engine/query_builder.py` — `build_price_query` (the proven §3 5-col query, values
  inlined injection-safely) + `build_catalog_names_query` for the dropdowns.
- `engine/sources/com.py` — extracted `Com1C` context manager (one segfault-safe
  teardown shared by price-fetch and discovery).
- Reviewer (Opus): query fidelity and segfault-safe teardown verified correct. Fixed
  M1 — `error_text` now redacts `Pwd=`/`Usr=` so a connection-string-echoing 1C error
  can't leak the password into logs/UI (test added). 52 tests pass (mocked COM).
**Live validation still required before Phase 1 sign-off (needs real 1C creds):**
- Discovery returns the expected warehouse/price-type lists; the builder query runs and
  returns rows matching the CLI for the same inputs.
- Confirm catalog hierarchy: `Справочник.Склады` hierarchical (exclude groups) vs
  `Справочник.ВидыЦен` flat (no `ЭтоГруппа`). If wrong, `discover()` raises a raw 1C error.
- Refine `describe_1c_error` heuristics against real 1C/COM error messages.

### Phase 2 — Cell engine (headless)
- CellRunner: run one cell end-to-end (query → exclusions → xlsx → upload → journal/state).
- Multi-cell run; per-cell run_history; pending/retry; staging mode.
- **Duplicate-across-warehouses detector**: warn when two enabled cells of the same cabinet
  can emit the same article (so the user avoids ZZap duplicates).
**Exit:** a configured set of cells uploads correctly (staging first, then real) and records
per-cell history; failures stage and retry.

### Phase 3 — GUI
- Connection screen (enter 1C creds, "Test connection", save encrypted).
- Cabinets screen (name + API key, test key).
- Cells table: add/edit/delete; warehouse & price-type dropdowns from 1C; code_templ input;
  per-cell enable; per-template checklist reminder (PROJECT_MEMORY §5); dup warning banner.
- Settings: interval hours, staging mode, autostart.
- Status/logs view (run_history + journal tail), "Run now" (one cell / all).
**Exit:** a non-technical user can configure everything without touching files.

### Phase 4 — Scheduler & background
- APScheduler interval job from settings; coalescing + misfire grace for catch-up.
- System tray: run in background, show next run, last results, quick "run now", open window.
- Autostart with Windows (per-user).
**Exit:** app uploads on schedule while minimized to tray; survives sleep/wake; honors interval
changes live.

### Phase 5 — Reliability, security, UX polish
- Robust error surfacing (toasts/notifications), retry/pending UX, log rotation.
- Secret handling review; least-privilege 1C user guidance; optional HTTPS notes for OData.
- Edge cases: locked files, partial 1C data, ZZap 4xx mapping to clear messages.
**Exit:** unattended for days without intervention; clear diagnostics when something breaks.

### Phase 6 — Packaging & delivery
- PyInstaller build, app icon, version, first-run wizard.
- Installer + autostart registration; uninstall cleanup.
- User guide (RU) + this roadmap kept current.
**Exit:** double-click install, configure, runs.

### Phase 7 — Optional / future
- Multiple 1C connections (the second cabinet's other base; OData source as an alternative
  to COM — engine already has an OData path to adapt).
- Per-cell schedules; CSV/other formats; export/import of configuration; multi-language.

---

## 6. Cross-cutting requirements

- **Safety first:** default to **staging mode** for any newly added cell; real upload only
  after the user confirms. Never silently publish.
- **No plaintext secrets.** Mask keys in the UI; encrypt at rest.
- **Idempotent & observable:** every run leaves a history row + journal line.
- **Reuse, don't rewrite** the proven engine; changes there need tests + review.
- **Windows/COM correctness:** 64-bit, per-thread CoInitialize, segfault-safe teardown,
  UTF-8 logging.
- **Respect the duplicate rule** (PROJECT_MEMORY §4): warn before a config can create ZZap dupes.

---

## 7. Definition of done (MVP)

A user installs the app, enters 1C admin credentials, the app lists their warehouses and
price types, they add 2+ cells (template + warehouse + price type), set "every N hours",
test in staging, then enable real uploads — and the app keeps each ZZap template in sync on
schedule, with visible per-cell status and safe retry on failures.
