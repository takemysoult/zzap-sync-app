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
- **Exclusions** — a per-cell list of article numbers NOT to upload. The user can type
  them OR **import from an Excel file (`.xlsx` / `.xls`)** (choose a column, optional
  header skip; articles normalized trim+upper, deduped). Stored as an `exclusion_list`
  the cell references; applied via `apply_exclusions` at run time.

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
  - **Source = COM** (confirmed 2026-06-23). OData stays a future/secondary path only.
  - Excel exclusions import needs **openpyxl** (`.xlsx`, already a dep) + **xlrd>=2.0.1**
    (`.xls`). xlrd 2.x reads only `.xls`; branch on file extension.
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

### Phase 2 — Cell engine (headless)  🟡 CODE COMPLETE (2026-06-23, mocked COM/HTTP; reviewed: SHIP-WITH-FIXES → fixed)
- `app/services/cell_runner.py:CellRunner` — runs one cell end-to-end
  (`build_price_query` → `ComPriceSource.fetch_rows` → `clean_rows` → `apply_exclusions`
  → `build_xlsx(include_header=False)` → `upload_price` → `run_history` + per-cell
  `Delivery`). Source + uploader are injected so it's tested with no live 1C/ZZap.
  - **Staging gate:** a real POST happens only when the GLOBAL `staging_mode` setting is
    OFF *and* the cell's `staging_mode` is OFF; otherwise the file is built and the run is
    `STAGED`. New cells default to staging.
  - **Failure model:** an upload that throws stages the file to pending + `FAIL` (retryable
    via `retry_pending` → `RESEND_OK`); a pre-upload error (config/query/fetch/build), or a
    failure where even staging the pending file fails, is `ERROR` (nothing to retry).
  - `run_all_enabled` runs every enabled cell (errors are isolated per cell).
- **Duplicate-across-warehouses detector** — `app/services/duplicates.py:find_duplicate_risks`
  flags same-cabinet / different-template enabled cell pairs (shared warehouse = high
  confidence; disjoint = possible). The GUI (Phase 3) renders these as a banner.
- **Exclusions import from Excel** — `engine/exclusions.py:read_exclusion_articles`
  (`.xlsx`/`.xlsm` via openpyxl read-only streaming; `.xls` via xlrd≥2.0.1; numeric cells
  like `12345.0` → `"12345"`; blanks dropped; optional header skip). DAL
  `import_exclusion_list` (normalize trim+upper+dedupe via `engine.transform.normalize_articles`,
  create/replace/append) + `assign_exclusion_list_to_cell`. End-to-end test: import → assign
  → a CellRunner run actually filters those articles.
- **DAL thread-affinity** — `Database` now sets `busy_timeout` + `journal_mode=WAL` (file
  DBs only); open one `Database`/`CellRunner` per worker thread (scheduler-safe for Phase 4).
- **Carry-over hardening (Phase 0/1 reviewer notes):** end-to-end test that a cabinet's
  stored `api_url` flows into `ZzapConfig` and the upload POSTs to it; fixed a secret-leak in
  `error_text` redaction (a `"` in the password is serialized as `""` and was partially
  leaking). 78 tests pass (mocked COM/HTTP).
**Exit (headless met; live send pending with Phase 1 live validation):** cells build + record
per-cell history; staging is enforced; failures stage and retry; an Excel exclusions file
imports and is applied. The first *real* ZZap POST is exercised against the live cabinet
together with Phase 1 live validation.

### Phase 3 — GUI  ✅ CODE COMPLETE (2026-06-23; headless smoke-tested offscreen)
- `app/gui/` — PySide6 app: a tabbed `MainWindow` over five screens, an `AsyncRunner`
  (QThreadPool) that runs all blocking work off the UI thread, and `AppContext`
  (UI-thread `Database` + a fresh per-worker `Database`; caches 1C discovery).
- Connection screen (enter 1C creds, "Test connection" off-thread, save encrypted).
- Cabinets screen (name + masked API key + api_url). No live "test key": ZZap has no cheap
  key-validation method (PROJECT_MEMORY §4), so a key is proven on the first real upload.
- Cell editor: cabinet/connection pickers, code_templ, warehouse (multi-select) & price-type
  dropdowns filled live from 1C (`discover`, off-thread, cached + merged with saved values so
  nothing is lost offline); per-cell enabled + staging toggles (new cells default to staging);
  exclusions editor with "Импорт исключений из Excel"; per-template checklist (PROJECT_MEMORY §5).
- Cells table: add/edit/delete; duplicate-risk banner from `find_duplicate_risks` (red =
  shared warehouse, amber = split); "Run now" (one / all enabled) via CellRunner on a worker
  thread (its own `Database`), with a confirmation before any real (non-staging) send.
- Settings: interval hours, the GLOBAL staging kill-switch, autostart.
- Status/logs view (`run_history` table + per-cell journal tail).
- **Threading & secrets:** worker results are delivered to bound `AsyncRunner` slots so Qt's
  AutoConnection becomes a *queued* (UI-thread) delivery; secrets are masked, never reloaded
  into fields (placeholder + update-only-if-typed), and every worker error is redacted via
  `error_text`. 6 offscreen smoke tests (incl. a UI-thread-delivery + secret-redaction guard).
**Exit met (config/run):** a non-technical user can configure connection/cabinets/cells and run
a cell from the UI without touching files. (Full live exercise pairs with Phase 1/2 validation.)

### Phase 4 — Scheduler & background (system tray)  ✅ CODE COMPLETE (2026-06-24; offline-tested + real-platform boot smoke)
**Модель выбрана (2026-06-23): планировщик ВНУТРИ приложения + системный трей** (НЕ Планировщик
заданий Windows). RU UI, safety-first, без бизнес-логики в планировщике (reuse `CellRunner`).
- **APScheduler внутри приложения** — интервальная задача из настроек (`interval_hours`),
  **coalescing + misfire grace**, чтобы пропуски при сне/выключении отрабатывались один раз при
  возобновлении (а не лавиной). Каждый тик: `run_all_enabled` (или по ячейке) + проход досыла.
- **Системный трей** (`QSystemTrayIcon`): работа в фоне, окно **сворачивается в трей** (а не
  выходит); меню (RU): время следующего запуска, итог последних загрузок, «Запустить сейчас»
  (все/ячейка), «Открыть окно», «Выход»; уведомления о результатах (RU).
- **Автозапуск с Windows** (per-user, `HKCU\...\Run`) — стартует свёрнутым в трей; вкл/выкл из
  Настроек добавляет/убирает запись.
- **Офлайн-восстановление и досыл после сбоя** (отключили свет/интернет → как появятся, всё
  уходит СРАЗУ):
  - *Пропущенные запуски* (ПК был выключен/спал): при старте и на каждом тике сверять время
    последней успешной выгрузки (state.json / `run_history`) с интервалом; если просрочено —
    выполнить **немедленно** (catch-up), не дожидаясь следующего планового тика.
  - *Неудачные отправки* (не было сети в момент выгрузки): файл уже откладывается в pending
    (`CellRunner.mark_pending`). Частый проход **flush pending** + повтор при восстановлении сети
    дошлёт отложенное **сразу**, как только связь вернётся (`RESEND_OK`).
  - *Офлайн-проверка «было ли отправление»*: по журналу/состоянию ячейки (`last_success`,
    наличие pending) видно, ушла загрузка или нет — без обращения к ZZap. (Онлайн-подтверждение
    числа опубликованных строк через `GET /stat/prices` — опционально позже, Phase 5.)
  - Перед боевой отправкой — лёгкая проверка доступности сети, чтобы не плодить лишние FAIL;
    авто-повтор pending по таймеру/при появлении сети.
- **Живое изменение интервала:** сохранение нового интервала в Настройках перепланирует задачу
  без перезапуска; UI отражает состояние планировщика (работает / следующий запуск).
- **Тесты:** логика планирования/перепланирования и автозапуска — с мокнутыми таймерами/реестром
  (без реальных таймеров и без живого 1С/ZZap); офлайн-catch-up проверяется по state/run_history.
**Exit:** приложение выгружает по расписанию свёрнутым в трей; переживает сон/выключение и обрыв
сети — пропущенные и отложенные выгрузки уходят сразу при возобновлении; уважает глобальный
staging-стоп-кран; стартует с Windows; интервал меняется на лету.

**Реализовано (2026-06-24):** `app/services/scheduler.py` (`SchedulerService` поверх APScheduler
`BackgroundScheduler`: интервальная задача `coalesce=True, misfire_grace_time=None, max_instances=1`;
частый проход досыла `flush_pending`; `threading.Lock` сериализует все запуски — плановый/ручной/
catch-up/flush не пересекаются; `is_overdue`/`last_run_at` — чистое решение о catch-up при старте;
`reschedule` меняет интервал на лету; `request_run_*` — одноразовые задачи для трея, не блокируя UI),
`app/services/net.py` (`is_online`), `app/services/autostart.py` (`AutostartManager` поверх
`HKCU\...\Run`, бэкенд реестра инжектируется), `app/gui/tray.py` (`QSystemTrayIcon` + мост Qt-сигнала
для доставки результатов на UI-поток). `CellRunner` получил необязательный `network_check`
(офлайн → pending без HTTP; по умолчанию None — поведение Phase 2 не меняется). Каждый запуск
открывает свой `Database` на рабочем потоке APScheduler; `Com1C` сам делает Co(Un)Initialize.
**Тесты:** 103 проходят офлайн (FakeScheduler — без реальных таймеров; реестр — словарный фейк),
плюс проверен реальный старт приложения (трей+планировщик+catch-up, чистый выход) и реальный
`HKCU\...\Run`. **Боевые плановые выгрузки сверяются вместе с live-валидацией Phase 1/2.**

### Phase 5 — Reliability, security, UX polish  ✅ CODE COMPLETE (2026-06-24; offline-tested; publish-confirmation deferred)
- Robust error surfacing (toasts/notifications), retry/pending UX, log rotation.
- Secret handling review; least-privilege 1C user guidance; optional HTTPS notes for OData.
- Edge cases: locked files, partial 1C data, ZZap 4xx mapping to clear messages.

**Реализовано (2026-06-24):**
- **Таксономия ошибок ZZap** (`engine/zzap_client.py`): `ZzapPermanentError` (401/403/404/413/
  400/прочие 4xx, `success:false`) vs `ZzapTransientError` (408/429/5xx, таймаут/нет сети) —
  понятные RU-сообщения с кодом. `CellRunner` теперь различает: постоянная ошибка ⇒ **ERROR**
  (без бесконечного досыла, видно «что чинить»), временная/неизвестная ⇒ **FAIL** + pending
  (как раньше). Существующий контракт payload не тронут.
- **Защита от пустой выгрузки** (`CellRunner.run_cell`): боевой POST из 0 строк ОТМЕНЯЕТСЯ
  (ERROR), т.к. загрузка ПОЛНОСТЬЮ заменяет шаблон и пустой файл его бы очистил (§4). В staging
  пустой файл по-прежнему просто собирается.
- **Ротация логов** (`app/logging_setup.py` + `paths.logs_dir()`): консоль + `RotatingFileHandler`
  → `%LOCALAPPDATA%\ZZapSync\logs\app.log` (UTF-8, 1 МБ × 5). `app.gui` использует её вместо
  `basicConfig`. Покрытие занятого файла (`build_xlsx` PermissionError → временное имя) уже было.
- **UX досыла/pending:** кнопка «Дослать отложенное» на вкладке «Ячейки» (досыл выбранной ячейки
  через `retry_pending`, под общим run-замком планировщика); индикатор «⏳ Ожидает досылки …» на
  вкладке «Журнал» для выбранной ячейки (чтение `state.json`, без обращения к ZZap).
- **Тесты:** 111 проходят офлайн (маппинг кодов/типы исключений, permanent⇒ERROR/0 строк⇒ERROR,
  ротация лога, индикатор pending). Проверен реальный старт приложения (лог пишется, catch-up
  отрабатывает, чистый выход).
- **Отложено до live-валидации:** подтверждение публикации через `GET /stat/prices` (форму ответа
  нужно проверить на живом кабинете) — реализуем неблокирующе и под флагом во время Section A.
- **Post-upload publish confirmation (optional, `GET /api/client/v1/stat/prices`)**: the
  only extra ZZap method worth adding later. After a successful upload, read back the number
  of *published* rows for the cell's `code_templ` and show it in the cell status — confirming
  the template was replaced, without the user opening the cabinet. Pairs with the §4 note
  that ZZap dedups server-side, so published < sent is normal (e.g. sent 4092 → published
  ~3976); surfacing both numbers makes that expected gap visible instead of alarming.
  Treat the endpoint as a *candidate* until its response shape is verified against the live
  cabinet (header `zzap-api-key`, same as upload); keep it non-blocking (a stat-read failure
  must never turn a successful `OK` upload into a `FAIL`).
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
