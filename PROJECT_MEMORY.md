# ZZap Sync App — Project Memory (carried-over knowledge)

> This file is the single source of truth for hard-won facts from the working CLI
> utility (`C:\Users\Admin\Desktop\zzap`). The new desktop app reuses its proven engine.
> **Do not re-derive these — they cost real debugging time.** Update this file as facts change.

---

## 0. What we are building

A **Windows desktop application** that replaces the manual `config.ini` + Task Scheduler
setup with a GUI. The user manages multiple upload "cells", each mapping
**(warehouse + price type)** from 1C → a **ZZap template (`code_templ`)**, and the app
uploads all enabled cells to ZZap on a configurable interval (every N hours).

Warehouses and price types are **pulled live from 1C** after the user enters 1C
credentials (admin / external-connection right).

The predecessor CLI is **already live in production** for cabinet #1 (template `330017019`),
uploading every 5h via Windows Task Scheduler. The app generalizes that to many
cells/cabinets with a UI.

---

## 1. Environment (verified, non-negotiable)

- OS: Windows 10 Pro x64. Shell: PowerShell + Git Bash.
- **Python must be 64-bit** and match the 1C platform bitness. Use the project's own
  64-bit venv. There was a 32-bit `.venv32` historically — **do not use it**.
- 1C platform **8.3.27 x64** is installed; `comcntr.dll` is x64.
- COM access uses **`win32com.client.dynamic.Dispatch`** (late binding) via `pywin32`.
- The COM connector typelib `{98AC3B5B-...}` had to be registered in **HKCU** (per-user)
  because `RegisterTypeLibForUser` was missing in this pywin32 build. See
  `register_typelib.py` in the old project — it reads `comcntr.dll` path from the registry
  (CLSID InprocServer32) for the matching bitness. Connector ProgID: `V83.COMConnector`.
- **Segfault-on-exit fix (critical):** null out every COM object (`selection = result =
  query = conn = connector = None`) and `gc.collect()` **before** `pythoncom.CoUninitialize()`.
  Otherwise finalizing COM objects after COM is uninitialized crashes the process.
- Windows console isn't UTF-8: run Python with `PYTHONIOENCODING=utf-8`, and reconfigure
  stdout/stderr to utf-8 in code, or Cyrillic logs break.
- COM is **apartment-threaded**: each thread doing COM must call `CoInitialize`/`CoUninitialize`.
  Long 1C queries must run on a worker thread/process to keep a GUI responsive.

## 2. The 1C base (cabinet #1 source)

- Config: **1С:Управление торговлей (УТ) 11.5** (11.5.22.180). Server base on Linux/Samba.
- Connection string shape (server base):
  `Srvr="Serv1C";Ref="ut2025";Usr="<user>";Pwd="<pass>";`
  `Serv1C` = `192.168.55.34`, ports 1540/1541. This PC is on `192.168.55.20`.
- The 1C user **must have the "Внешнее соединение" (External connection) right** (COM).
  Account `Натали` works. A user without that right → "Внешнее соединение не разрешено".
- A second ZZap cabinet will pull from a **different 1C base** that this PC may not reach
  via COM → OData or running on that machine was being evaluated. The app should not assume
  a single base; design for multiple connections eventually (see roadmap).

## 3. Data model & 1C query (verified by reconciliation with the 1C "Прайс-лист" report)

- Output file is **strictly 5 columns, in order**:
  `Производитель | Номер | Наименование | Количество | Цена`.
- **Price type** for ZZap is a dedicated price type named **"ZZap"**.
- **Producer/brand** = `Справочник.Производители` via `ЕСТЬNULL(Ном.Производитель.Наименование, "")`
  (NOT Партнёры). Main table alias is **`Ном`** (avoids "ambiguous field" with `Номенклатура`).
- **Warehouses**: sales-only whitelist. For cabinet #1 those are
  `"Квант (Новые) 3 этаж"`, `"Квант (Комиссия) 3 этаж"`, `"Товары по партиям"`.
  Service warehouses (Оригинал, Потеряшки, БРАК, Виртуальный склад, Женя, Склады) are excluded.
- **Filters**: not a folder, not deletion-marked, `qty > 0`, `ZZap price > 0`.
- **Stock** = `РегистрНакопления.ТоварыНаСкладах.Остатки`, resource `ВНаличииОстаток`, summed
  per Номенклатура with a warehouse filter.
- **Price** = `РегистрСведений.ЦеныНоменклатуры.СрезПоследних(, ВидЦены.Наименование = "ZZap")`
  taken as of *now* (latest slice).
- Proven query (cabinet #1) — the app must build this **dynamically** per cell from the
  chosen warehouse(s) + price type:
  ```
  ВЫБРАТЬ
    ЕСТЬNULL(Ном.Производитель.Наименование, "") КАК Производитель,
    Ном.Артикул КАК Номер,
    Ном.Наименование КАК Наименование,
    ЕСТЬNULL(Остатки.Количество, 0) КАК Количество,
    ЕСТЬNULL(Цены.Цена, 0) КАК Цена
  ИЗ Справочник.Номенклатура КАК Ном
    ЛЕВОЕ СОЕДИНЕНИЕ (
      ВЫБРАТЬ Ост.Номенклатура КАК Номенклатура, СУММА(Ост.ВНаличииОстаток) КАК Количество
      ИЗ РегистрНакопления.ТоварыНаСкладах.Остатки(, Склад.Наименование В (&Склады)) КАК Ост
      СГРУППИРОВАТЬ ПО Ост.Номенклатура) КАК Остатки
      ПО Остатки.Номенклатура = Ном.Ссылка
    ЛЕВОЕ СОЕДИНЕНИЕ РегистрСведений.ЦеныНоменклатуры.СрезПоследних(, ВидЦены.Наименование = &ВидЦены) КАК Цены
      ПО Цены.Номенклатура = Ном.Ссылка
  ГДЕ НЕ Ном.ПометкаУдаления И НЕ Ном.ЭтоГруппа
    И ЕСТЬNULL(Остатки.Количество, 0) > 0 И ЕСТЬNULL(Цены.Цена, 0) > 0
  ```
  (Use parameters for `&Склады` / `&ВидЦены`, or build safely. Reconciliation: ~4154
  articles matched the 1C report exactly; remaining deltas were snapshot-time differences,
  not bugs. Duplicate article numbers across brand cards are legitimate — different offers.)
- **Dropdowns** are populated from: warehouses `Справочник.Склады`, price types
  `Справочник.ВидыЦен` (filter out deletion-marked / folders). See old `explore_1c.py`.

## 4. ZZap REST API (WORKING — verified by a real production upload, 2026-06-22)

- **Endpoint:** `POST https://b52-api.zzap.pro/api/client/v1/price1c/upload` (section "Прайс 1С").
  - NOT `.../v1/price_upload` (returns **404**). NOT `.../v1/upload/template/price` (old).
  - OpenAPI/Swagger: `https://b52-api.zzap.pro/swagger/v1/swagger.json` (49 methods).
- **Auth:** header **`zzap-api-key`** (scheme `zzap1`, apiKey in header). Key format
  `zzap1_...` (~272 chars). One key per cabinet/account.
- **Body (JSON):**
  `file_name`, `file_body` (base64 of the XLSX), `code_templ` (int),
  **`part_num` and `part_total` are REQUIRED** and **0-indexed**: whole file =
  `part_num=0, part_total=1`.
  - Missing part_total → `400 "Неверное значение part_total"`.
  - `part_num=1` → `400 "Missing blocks: MDAwMDAw"` (base64 "000000" = block 0 missing).
- **Success:** HTTP 200 `{"success":true,"code":200,"errors":{}}` (result usually empty,
  no file_url).
- **No test mode** on the method. The CLI emulates it: `test_mode=true` = build the file but
  do not POST. Keep this safety switch in the app (a global "dry run / staging" mode).
- ZZap **deduplicates rows with identical article+brand** server-side, so the published row
  count can be **lower** than what we send (e.g., sent 4092, published ~3976). Normal.
- A new upload to a `code_templ` **fully replaces** that template's price list (no append).
- **Duplicate rule:** the same goods present in **two different templates of the SAME cabinet**
  show up twice in the cabinet. Splitting by warehouse where an item exists on both
  warehouses → duplicates. The app MUST detect/warn about this.

## 5. ZZap template configuration (done by the user in the cabinet)

Each `code_templ` must be set (in the ZZap cabinet UI) to:
- Type = **"Загрузка прайса через API"**.
- Column mapping = **1=Производитель, 2=Номер, 3=Наименование, 4=Количество, 5=Цена**.
- Data starts at **row 1** (no header row) → our file is written with `include_header=false`.
The app cannot set this via API (no such method) — surface it as a checklist/reminder per cell.

## 6. Reliability model (reuse from CLI)

- **Journal** (`upload_journal.log`): one line per event — `OK`, `FAIL`, `RESEND_OK`, `ERROR`.
- **State** (`state.json`): last success timestamp + row count (per cell in the app).
- **Pending/retry**: on send failure the built file is staged and re-sent later (a frequent
  "flush" pass). The app's scheduler should retry pending cells independently.
- **Catch-up**: missed runs (PC off/asleep) should run when possible (Task Scheduler's
  `StartWhenAvailable` analog, or APScheduler misfire handling).
- File-busy handling: if the local xlsx is locked, save under a timestamped name.

## 7. Reusable engine (the old project)

Proven, tested library at `C:\Users\Admin\Desktop\zzap\zzapsync\`:
- `sources/com.py` — COM connect + run query → `PriceRow[]` (has the segfault fix).
- `transform.py` — `clean_rows`, `build_xlsx` (column layout, busy-file fallback),
  `load_exclusions` / `apply_exclusions` (exclude.txt mechanism).
- `zzap_client.py` — `upload_price` (the API call; already updated with part_num/part_total).
- `delivery.py` — journal / state / pending.
- `config.py`, `models.py` (`PriceRow`).
Also `register_typelib.py`, `explore_1c.py` (warehouse/price-type discovery), `check_com.py`.
**Plan:** extract this into a shared `engine` package for the app (adapt config from
ini-file to the app's data store; keep the COM/transform/zzap_client/delivery logic).

## 8. Decisions made by the architect (Phase 0, 2026-06-23)

Stack confirmed (no deviation from ROADMAP §2): **PySide6 + APScheduler + SQLite +
Windows DPAPI + PyInstaller, Python 3.12 x64**. Git initialized (`main`).
- **Engine extracted** to `engine/` (canonical, unit-tested). The old `zzapsync/` seed
  was removed from this repo (it still lives untouched in the old CLI project). The
  `config.ini` loader was dropped — the app builds `ComConfig`/`ZzapConfig`/`OutputConfig`
  from the SQLite store. The ZZap `api_url` default was corrected to `price1c/upload`.
- **COM behind an interface:** `engine/sources/base.py:PriceSource` (Protocol);
  `ComPriceSource` keeps the segfault-safe teardown; pywin32 import deferred to call time.
- **Secrets:** `app/security/secrets.py` — DPAPI (`DpapiCipher`) + a `Cipher` protocol so
  the DAL is unit-testable without DPAPI. DAL encrypts on write, stores BLOBs, never logs
  plaintext; partial updates preserve the existing secret.
- **Data store:** `app/db` — schema (ROADMAP §4) + DAL + `user_version` migration.
  New cells default to **staging** (`staging_mode=1`) and `enabled=0` (safety).

### Still open (later phases)
- Process model nuance: APScheduler in the tray app vs an extra Windows Task backstop (Phase 4).
- Multi-cabinet / multi-1C-connection UX (schema already supports multiple rows; Phase 7).
- Duplicate-across-warehouses detection UX (Phase 2 detector + Phase 3 banner).
- DAL thread-affinity once the scheduler runs jobs on worker threads (Phase 2).
