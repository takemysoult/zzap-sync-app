# Seed notes — what was copied into this folder

Copied from the working CLI project `C:\Users\Admin\Desktop\zzap` as the starting engine
for the desktop app. Read `PROJECT_MEMORY.md` and `ROADMAP.md` first.

## What's here (reusable, known-good)
- **`zzapsync/`** — the proven engine package (seed). Internal imports are all relative, so
  it's self-contained and importable as-is.
  - `sources/com.py` — COM connect + run query → `PriceRow[]` (segfault-safe teardown).
  - `sources/odata.py` — OData source (for the future 2nd base / alt path).
  - `transform.py` — `clean_rows`, `build_xlsx` (5-col layout, busy-file fallback),
    `load_exclusions`/`apply_exclusions`.
  - `zzap_client.py` — `upload_price` (correct `price1c/upload` + `part_num=0/part_total=1`).
  - `delivery.py` — journal / state / pending.
  - `config.py`, `models.py` (`PriceRow`).
- **`check_com.py`** — minimal COM connection test (reference for ConnectionManager).
- **`explore_1c.py`** — discovers warehouses (`Справочник.Склады`) and price types
  (`Справочник.ВидыЦен`). This is the blueprint for the app's live dropdowns (Phase 1).
- **`register_typelib.py`** — registers the COM connector typelib in HKCU (needed once on a
  machine for `V83.COMConnector` to resolve). Standalone.
- **`requirements.txt`** — base deps (pywin32, openpyxl, requests). The app will add
  PySide6, APScheduler, etc.

## Deliberately NOT copied (and why)
- **`config.ini`** — contains the **LIVE ZZap API key and the 1C password** for cabinet #1.
  Secrets must not be duplicated. The app stores secrets encrypted (DPAPI) in its own store.
- Runtime artifacts: `state.json`, `upload_journal.log`, `zzap_sync.log`, `pending/`,
  generated `price*.xlsx` — these belong to the running cabinet-#1 instance.
- `exclude.txt` — cabinet-#1-specific exclusion list (the *mechanism* is in `transform.py`;
  the app manages exclusion lists in its data store).
- `.venv` / `.venv32`, installers/logs (`1c_install*`, `install_1c.ps1`), one-off diagnostics
  (`smoke_test.py`, `compare_prices.py`, `_probe.py`, `run.bat`, `sync.py`).
  - `sync.py` (the old CLI orchestrator) is a good **reference** for the end-to-end flow —
    read it in the old folder if useful, but the app replaces it with the CellRunner +
    scheduler.

## Phase 0 actions (architect) — ✅ DONE 2026-06-23

> The seed `zzapsync/` has been extracted into the canonical, unit-tested `engine/`
> package and then **removed** from this repo. `check_com.py` / `explore_1c.py` /
> `register_typelib.py` moved to `reference/` (blueprints for Phase 1). The original
> tasks below are kept for context.

1. Create a **64-bit** venv here and `pip install -r requirements.txt` (then add app deps).
2. Sanity-check the engine imports (done once already: `import zzapsync` resolves).
3. Refactor `zzapsync/` → `engine/` **with unit tests** (mock COM + HTTP), per ROADMAP §5.
4. Replace the old `config.ini`-based config flow with the app's **SQLite + DPAPI** store.
   - `check_com.py` / `explore_1c.py` loaded credentials from the old `config.ini` (now
     absent) — adapt them to the new ConnectionManager; keep the COM/query logic.
5. Never write secrets to disk in plaintext. The live cabinet-#1 key stays only in the old
   project's `config.ini`, untouched.
