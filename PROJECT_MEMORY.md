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

### Phase 1 (2026-06-23, mocked-COM; live validation deferred)
- Dynamic query builder lives in `engine/query_builder.py` (`build_price_query` = the §3
  query with warehouse/price-type values inlined as escaped 1C literals — the engine's COM
  layer does not bind parameters, same as the CLI).
- Single COM connection context: `engine/sources/com.py:Com1C` (used by both price-fetch and
  discovery; segfault-safe teardown lives here now).
- `ConnectionManager` (`app/services/connection.py`) connects/validates/discovers. Security:
  `error_text` redacts `Pwd=`/`Usr=` because some 1C COM errors echo the connection string.
- **Unverified vs live base:** Склады assumed hierarchical (exclude groups), ВидыЦен assumed
  flat. Confirm on cabinet #1 before relying on the dropdowns.

### Phase 2 (2026-06-23, headless; mocked COM/HTTP; reviewed: SHIP-WITH-FIXES → fixed)
- **CellRunner** (`app/services/cell_runner.py`): runs one cell end-to-end; never raises —
  every outcome is a `RunResult` + a `run_history` row. COM source (`source_factory`) and
  HTTP uploader are injected (defaults `ComPriceSource`/`upload_price`) → fully unit-tested.
- **Staging is the safety gate:** effective real POST ⇔ `NOT global staging AND NOT
  cell.staging_mode`. Global flag = the `setting` key **`staging_mode`** (default OFF;
  per-cell `staging_mode` defaults ON, so new cells never publish silently). Otherwise the
  file is built and the run is `STAGED` (no POST).
- **Status taxonomy:** `OK` (posted), `STAGED` (built, not sent), `FAIL` (upload threw →
  file staged to pending, retryable via `retry_pending` → `RESEND_OK`), `ERROR`
  (config/query/fetch/build failed, OR pending-staging itself failed → nothing to retry).
  Row count is carried in the pending state so a resend reports real `rows_sent`.
- **Secrets on error paths:** every exception message is flattened through
  `connection.error_text` (redacts `Pwd=`/`Usr=`) before it touches a log, `run_history`,
  journal, or `RunResult`. A 1C COM error can echo the conn string (incl. the password) —
  this is the guard. Fixed a redaction gap: a `"` in the password serializes as `""`
  (`Pwd="a""b"`) and the old regex leaked the tail; the value branch now consumes `""`.
- **Duplicate rule (§4) implemented** in `app/services/duplicates.py:find_duplicate_risks`:
  same cabinet + DIFFERENT `code_templ` + both enabled ⇒ dup risk (shared warehouse = high
  confidence, disjoint = possible). Same `code_templ` is NOT a dup (the runs overwrite each
  other). Warehouse compare is case-insensitive; output deterministic (cabinet id, then cell id).
- **DAL thread-affinity:** chose **per-thread `Database` + WAL + `busy_timeout=5000`** (not
  a shared connection + lock). WAL is file-only (skipped for `:memory:`). Migrations run on
  first open (main thread) before workers connect.
- **Excel exclusions import:** `engine/exclusions.py:read_exclusion_articles` (.xlsx/.xlsm =
  openpyxl read-only streaming + `wb.close()`; .xls = **xlrd≥2.0.1**, .xls-only; numeric
  cell `12345.0` → `"12345"`; blanks dropped; optional header skip; NO normalization here).
  Normalization (trim+upper+dedupe) lives in `engine.transform.normalize_articles` and is
  reused by the DAL `import_exclusion_list` (create/replace/append; append preserves manual
  `#` comments) + `assign_exclusion_list_to_cell`. `apply_exclusions` re-normalizes at run
  time via the same `_norm_article`, so import-time and match-time always agree.

### Phase 3 (2026-06-23, GUI; PySide6; headless smoke-tested offscreen)
- **Package `app/gui/`**: `app.py`/`__main__.py` (entry `python -m app.gui`), `main_window.py`
  (tabbed shell), `context.py` (`AppContext`), `workers.py` (`AsyncRunner`), `screens/*`. New
  `app/paths.py` resolves per-user data: DB + per-cell work dir under `%LOCALAPPDATA%\ZZapSync`
  (override via `ZZAP_APP_DATA` — tests use it). Only `app.gui.*` imports PySide6; engine/services
  stay Qt-free.
- **Threading (the subtle one):** `AsyncRunner.submit` runs the callable on a `QThreadPool`
  worker and connects the worker's signals to **bound methods of AsyncRunner** (a UI-thread
  QObject). That matters: Qt AutoConnection picks queued-vs-direct from the *receiver's* thread
  affinity, so a bound-method receiver on the UI thread ⇒ **queued delivery on the UI thread**.
  Connecting a signal to a bare lambda/closure instead = *direct* connection = callback runs on
  the worker thread = touches widgets off-thread = crash. A smoke test asserts the callback's
  `QThread.currentThread()` is the main thread (it would fail under the bare-closure bug).
- **DB per worker thread:** the UI thread uses `AppContext.db`; every worker job opens its own
  `Database` via `AppContext.new_db()` (DAL is thread-affine; WAL + busy_timeout from Phase 2
  make the connections coexist). "Run now" / "Run all" build a `CellRunner` inside the worker.
- **Secrets in the UI:** password / API-key fields are `EchoMode.Password`, are **never**
  loaded back from storage (a placeholder shows a secret is stored), and the stored secret is
  replaced only if the user types a new value (maps to DAL `update_password` /
  `update_api_key`). Every worker error reaches the UI through `error_text` (redacts `Pwd=`/`Usr=`).
- **Safety in the UI:** new cells default to staging + disabled (the `Cell()` model defaults);
  a Run that would actually POST (global staging OFF *and* cell staging OFF) is confirmed first.
- **No live ZZap key test:** there is no cheap key-validation method (§4) — a key is proven by
  the first real upload; the cabinet screen intentionally has no "test key" button.
- **Discovery cache:** `discover()` (warehouses/price types) runs off-thread and is cached on
  `AppContext.discovery`; the cell editor merges cached lists with the cell's saved values so an
  offline 1C never drops a previously-chosen warehouse/price type.

### Phase 4 (2026-06-24, scheduler + system tray; offline-tested + real-platform boot smoke)
- **`SchedulerService`** (`app/services/scheduler.py`, Qt-free, scheduler injected) over APScheduler
  `BackgroundScheduler`. Main interval job built from `interval_hours` (default 5) with
  **`coalesce=True, max_instances=1, misfire_grace_time=None`** — `None` = run no matter how late,
  so a sleep/wake misfire fires **once** on resume (catch-up path #1). A frequent `flush_pending`
  job (every 10 min) re-sends pending files (offline-send recovery).
- **One `threading.Lock` serialises every run path** (scheduled tick / manual / catch-up / flush) so
  two batches never interleave COM or double-upload a cell. Manual runs from the open window go
  through `run_under_lock` (the cells screen still delivers its own result via `AsyncRunner` and does
  NOT emit a tray notification); flush uses a **non-blocking** acquire (skips if a run holds the lock).
- **Catch-up decision is pure + unit-tested:** `last_run_at(db)` = max `run_history.started_at`
  (covers scheduled+manual); `is_overdue(last, interval, now)` (`None` or elapsed ⇒ True). `start()`
  runs `request_run_all("catchup")` immediately when overdue (catch-up path #2: process was fully off,
  not just asleep). The two paths are independent belt-and-suspenders.
- **Threading:** APScheduler runs jobs on its OWN worker threads (off the UI thread); each job opens
  its own `Database` via the injected `db_factory` (= `AppContext.new_db`; WAL makes per-thread
  connections coexist) and `Com1C` does its own Co(Un)Initialize — so COM is safe there. Results reach
  the UI for tray notifications via a Qt **signal bridge** (`app/gui/tray.py` `_Bridge.run_finished`):
  the service `listener` (set post-construction via `set_listener`) emits, AutoConnection delivers it
  **queued on the UI thread** — same rule as `AsyncRunner` (a bare-closure connection would run on the
  worker thread). A smoke test asserts UI-thread delivery.
- **`network_check`** (`app/services/net.py:is_online`, TCP to the ZZap host) is injected into
  `CellRunner`: when known-offline at upload time the file is staged to pending **without** an HTTP
  attempt (shared `_stage_pending` path → FAIL/retry). **Default `None` ⇒ Phase 2 behaviour unchanged.**
  The flush job also skips when offline (don't hammer a dead link).
- **Autostart** (`app/services/autostart.py`): per-user `HKCU\Software\Microsoft\Windows\
  CurrentVersion\Run` value `ZZapSync`; registry access behind an injectable backend (real
  `WinRegBackend` / dict fake in tests). Toggled from Настройки; launch command =
  `pythonw -m app.gui --minimized` (dev) / `"<exe>" --minimized` (frozen, Phase 6). Saving the
  interval **reschedules live** (`reschedule`, no restart).
- **Tray / window:** `QSystemTrayIcon` (icon painted at runtime; real asset deferred to Phase 6), RU
  menu (next run, last-results, «Запустить сейчас» все/ячейка, «Открыть окно», «Выход»), RU run
  notifications. App runs minimized to tray; `closeEvent` **hides** to tray (one-time RU hint, persisted
  via `tray_hint_shown`) only when `_background_active`. `app.setQuitOnLastWindowClosed(False)` so a
  close-to-tray doesn't quit — **but** if no system tray is available, `start_background` re-enables
  quit-on-close and keeps the window visible (else the app would be unreachable). «Выход» quits.
- **Tests:** 103 pass offline — `test_scheduler.py` (FakeScheduler: no real timers; asserts job
  triggers/`coalesce`/`misfire`, live reschedule, `is_overdue`, run/staging, catch-up overdue-vs-recent,
  flush skip-when-offline/busy, flush resends pending), `test_autostart.py` (dict backend), a CellRunner
  offline test, and a tray UI-thread-delivery smoke. Also verified: real-platform boot (tray+scheduler+
  catch-up, clean exit) and a real `HKCU\...\Run` round-trip.

### Phase 5 (2026-06-24, reliability/security/UX polish; offline-tested)
- **ZZap error taxonomy** (`engine/zzap_client.py`): `ZzapError` base + `ZzapPermanentError`
  (401/403/404/413/400 and other 4xx, plus a 200 `success:false`) vs `ZzapTransientError`
  (408/429/5xx, and `requests` ConnectionError/Timeout wrapped at the call site). One
  `_raise_for_zzap(resp)` maps status → a clear **RU** message (HTTP code kept in the text). The
  fixed payload/headers are untouched (§4). `CellRunner._upload` branches on type: **permanent ⇒
  ERROR (no pending — retrying can't fix a bad key/url/content; surfaces the actionable message);
  transient/unknown ⇒ FAIL + `_stage_pending` (retry)**. `retry_pending` left as catch-all FAIL (a
  permanent error during a flush just keeps the file pending — a visible "fix config" signal).
- **Empty-result guard** (`CellRunner.run_cell`, AFTER the staging check, BEFORE `_upload`): a real
  (non-staging) send with `rows_built == 0` is refused as **ERROR** — a ZZap upload FULLY REPLACES
  the template (§4), so an empty file would silently wipe it. Staging with 0 rows still just builds.
- **Log rotation** (`app/logging_setup.py` + `paths.logs_dir()`): console + `RotatingFileHandler` →
  `%LOCALAPPDATA%\ZZapSync\logs\app.log` (UTF-8 per §1; 1 MB × 5). `setup_logging()` is idempotent
  (handlers tagged `_zzap_tag`); `app/gui/app.py` calls it instead of `basicConfig`.
- **Pending/retry UX:** «Дослать отложенное» button on the Ячейки tab (`retry_pending` for the
  selected cell, off-thread via `AsyncRunner`, through the scheduler's `run_under_lock`); a
  «⏳ Ожидает досылки …» indicator on the Журнал tab from the cell's `state.json` pending (the
  offline "did it send?" check — no ZZap round-trip).
- **Tests:** 111 pass offline (code→type mapping incl. a network error; permanent⇒ERROR & 0-row⇒
  ERROR; log rotation+idempotency; pending indicator). Real app boot re-verified.
- **Deferred to live validation:** publish confirmation `GET /api/client/v1/stat/prices` (response
  shape unverified offline — implement non-blocking + gated during Section A).

### Phase 6 (2026-06-24, packaging + production hardening; built & released)
- **Staging removed (user decision):** per-cell `staging_mode` + the global kill-switch are gone from
  the model, schema, DAL, `CellRunner`, scheduler and GUI. Every enabled cell uploads for real on
  schedule. The leftover `cell.staging_mode` column on OLD DBs is harmless (never read). Remaining
  safety: the **0-row guard** (0 rows ⇒ ERROR, never wipe a template) + new cells default disabled +
  a confirm on manual «Запустить».
- **Single instance** (`app/single_instance.py`): `QSharedMemory("ZZapSyncSingleton")` (Windows frees
  it on crash) + `QLocalServer`/`QLocalSocket`; a 2nd launch pings the primary to `bring_to_front` and
  exits. Wired in `app/gui/app.py` after the `--watchdog` branch.
- **Crash watchdog:** the app writes `paths.heartbeat_path()` every 60 s (`watchdog.write_heartbeat`
  via a QTimer in `MainWindow._start_heartbeat`). `app/watchdog.py:run_watchdog` (entry
  `app.gui --watchdog`, no GUI): if `watchdog_enabled` off → exit; heartbeat fresh (<5 min) → exit;
  else relaunch `--minimized` + `ctypes` MessageBox. `app/services/watchdog_task.py` creates/removes a
  per-user interactive (`/it`) Scheduled Task "ZZapSync Watchdog" via `schtasks` (runner injected for
  tests), every `interval_hours` — the only thing that survives a native COM/Qt crash. Ensured on
  startup (`MainWindow._ensure_watchdog_task`); Настройки toggle (`watchdog_enabled`, default ON) +
  interval-save create/update/remove it.
- **Packaging** (`packaging/`): `zzapsync_main.py` (frozen entry), `zzapsync.spec` (PyInstaller onedir,
  `console=False`, icon, `datas=[('app/db/schema.sql','app/db')]`, hiddenimports = win32timezone +
  apscheduler executors/jobstores + lazily-imported app.watchdog/single_instance), `make_icon.py`→
  `zzapsync.ico` (Pillow), `installer.iss` (Inno Setup, Program Files / admin / x64compatible /
  Russian; uninstall removes the watchdog task + HKCU Run key), `build.ps1`. `.gitignore` overrides the
  `*.spec` ignore to keep `packaging/zzapsync.spec`. `app.__version__ = "1.0.0"`.
- **Release:** `dist/ZZapSync-Setup-1.0.0.exe` (~43 MB) built + uploaded to GitHub Releases `v1.0.0`.
- **Autostart** targets `sys.executable` when frozen (the installed exe). After go-live, **disable the
  old CLI's Windows Task Scheduler job** (same template 330017019) to avoid double uploads.

### E-mail delivery (2026-07-16, v1.2.0, branch email-delivery)
- **Второй канал доставки:** `cell.target` = `'zzap'` (по умолчанию; поведение прежнее) или
  `'email'` — собранный XLSX уходит письмом-вложением с ящика пользователя. Новая таблица
  `email_account` (schema **v3**; SMTP host/port/security='ssl'|'starttls'|'none', login,
  from_addr, password_enc — DPAPI как у остальных секретов) + колонки cell
  `target/email_account_id/email_to/email_subject` (ALTER TABLE, additive; старые ячейки
  получают target='zzap'). Получатели — у ЯЧЕЙКИ (`email_to`, запятая/`;`/пробел),
  ящик-отправитель — общий справочник (вкладка «Почта»).
- **`engine/email_client.py`** — зеркало zzap_client: `send_price_email` (smtplib +
  EmailMessage, XLSX как правильный MIME-тип), `send_test_email` (проверка входа с
  вкладки «Почта» — у почты, в отличие от ZZap, есть дешёвая проверка учётных данных),
  `parse_recipients`. Таксономия: `EmailPermanentError` (auth 535 — текст напоминает про
  «пароль приложения» для Mail.ru/Яндекс/Gmail; отклонённые адреса; 5xx) vs
  `EmailTransientError` (connect/timeout/disconnect/4xx) — CellRunner обрабатывает оба
  канала одной логикой (permanent⇒ERROR, transient⇒pending+досыл). SMTP-клиент
  инжектируется (`smtp_factory`) — тесты без сети.
- **CellRunner:** `_upload` ветвится по target (email: `_email_config` возвращает
  `(cfg|None, причина)`); `retry_pending` досылает pending тем же каналом, что и ячейка;
  0-строк-guard действует и для почты (пустой прайс = ошибка конфигурации). Почтовая
  ячейка НЕ требует кабинета (`_build` проверяет ящик+получателей вместо него);
  `result_cell` в редакторе обнуляет поля чужого канала, поэтому детектор задвоений
  (фильтр `cabinet_id is not None`) почтовые ячейки не видит.
- **GUI:** новая вкладка «Почта» (`app/gui/screens/email_accounts.py`, пресеты
  Mail.ru/Яндекс/Gmail — всем нужен пароль приложения; пароль маскируется и не
  перечитывается — правило как у API-ключа), в редакторе ячейки combo «Куда отправлять»
  (в Qt5 нет `QFormLayout.setRowVisible` — строки прячутся через `labelForField`).
  Тесты: 191 (было 164), новые test_email_client / test_email_dal /
  test_cell_runner_email + smoke редактора.

### Still open (later phases)
- **Phase 4 process model — DONE (2026-06-24):** see the Phase 4 subsection above. (Decision was
  locked 2026-06-23: APScheduler inside the app + system tray, NOT the Windows Task Scheduler.)
- **Phase 5 — DONE (2026-06-24)** except the deferred `GET /stat/prices` publish confirmation.
- **Phase 6 — DONE (2026-06-24):** packaging + staging removal + single-instance + watchdog + release.
- Multi-cabinet / multi-1C-connection UX (schema already supports multiple rows; Phase 7).
- Duplicate-across-warehouses detection UX (Phase 2 detector ✅ + Phase 3 banner).
- **Phase 1 + first real ZZap POST still need live validation** against cabinet #1 (real
  1C creds + reachable base). The headless engine is done; the live send is the sign-off.
