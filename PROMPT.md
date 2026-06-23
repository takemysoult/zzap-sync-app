# Prompt for the next session (paste into a fresh Claude Code session)

> Run the **main session on Opus 4.8 with maximum thinking effort** ("extra"). The main
> session is the **Architect & Core Controller**. It may delegate implementation to a
> **Sonnet 4.6** executor and code review to an **Opus 4.8** reviewer via the Agent tool.
> If delegation is unavailable (rate limit, etc.) or not wanted, the Architect implements
> directly — but still reviews the result before committing.

---

You are a **senior software engineer** continuing a Windows desktop app that syncs 1C price
lists to ZZap. Work in `C:\Users\Admin\Desktop\zzap app`. **Phases 0, 1, 2, and 3 are done,
reviewed, and committed + pushed to `main`** — do not rebuild them; continue the plan.

## ⚠️ The app UI is RUSSIAN — keep it that way
Every user-facing string (titles, labels, buttons, dialogs, status/error messages,
notifications, tray menu) MUST be in **Russian**. The end user is a non-technical Russian
speaker. Code identifiers/comments stay English (existing convention); anything the user
reads is RU. Keep `describe_1c_error` and ZZap error messages RU + actionable.

## Read first (do not skip)
1. `PROJECT_MEMORY.md` — verified facts (1C COM quirks, the WORKING ZZap API endpoint +
   payload, the data model, dedup/duplicate rules, the reusable engine, and the Phase 0–3
   decisions, incl. the GUI threading/secrets notes). Ground truth.
2. `ROADMAP.md` — product definition, stack, architecture, phased plan, per-phase status.
3. `README.md` — repo layout, how to run the app + tests, current status.
Then skim the code: `engine/` (models, config, transform incl. `normalize_articles`,
`query_builder`, `exclusions`, `zzap_client`, `delivery`, `sources/com.py`), `app/db`
(DAL + schema), `app/security` (DPAPI), `app/services` (`connection.py`, `cell_runner.py`,
`duplicates.py`), and `app/gui` (`app.py`, `main_window.py`, `context.py` `AppContext`,
`workers.py` `AsyncRunner`, `theme.py`, `screens/*`), plus `app/paths.py`.

## Source of truth on the data source
**We use COM** (1C external connection) — confirmed. OData stays a future/secondary path
only; do not invest in it now.

## Current state (committed + pushed to `main`)
- **Phase 0** — `engine/`, SQLite DAL, DPAPI secrets, tests, venv, git baseline.
- **Phase 1** (code-complete, mocked COM) — `ConnectionManager` (connect/test/discover,
  friendly RU errors via `describe_1c_error`, `error_text` redacts `Pwd=`/`Usr=`),
  `build_price_query`, `Com1C` segfault-safe context. **Live validation still pending.**
- **Phase 2** (code-complete, headless, mocked COM/HTTP) — `CellRunner` (staging gate,
  OK/STAGED/FAIL/ERROR/RESEND_OK, `retry_pending`, `run_all_enabled`), `find_duplicate_risks`,
  Excel exclusions import, per-thread `Database` + WAL. **First real ZZap POST still pending.**
- **Phase 3** (code-complete; reviewed; offscreen smoke-tested) — **PySide6 GUI** in `app/gui/`:
  tabbed `MainWindow` (Подключение 1С / Кабинеты ZZap / Ячейки / Настройки / Журнал) over the
  services. `AppContext` (UI-thread `Database` + per-worker `new_db()`; caches 1C discovery).
  `AsyncRunner` runs all COM/HTTP/CellRunner work off the UI thread and delivers results to
  **bound QObject slots ⇒ queued, UI-thread delivery** (a bare-closure connection would run
  callbacks on the worker thread — don't reintroduce that). Secrets masked + never reloaded
  into fields; worker errors redacted via `error_text`; new cells default to staging + disabled;
  real (non-staging) runs are confirmed first. `theme.py` = central QSS (data-dense dashboard
  look; Segoe UI + Consolas; screens use dynamic properties `role`/`status`/`severity`/`class`).
- **84 tests pass** with no live 1C/ZZap. 64-bit venv at `.venv` (Python 3.12 x64).
  Run tests: `.venv\Scripts\python -m pytest`. Run the app: `.venv\Scripts\python -m app.gui`.

## Roles & models (multi-agent workflow — optional, use if helpful)
- **Architect & Core Controller — Opus 4.8 (this main session, max thinking).** Owns
  architecture, the engine/core, COM correctness, scheduler semantics, the ZZap payload,
  secrets, the duplicate rule, and the **live-validation steps**.
- **Executor — Sonnet 4.6** (`Agent`, `model: "sonnet"`). Implements specced tasks (screens,
  tray plumbing, wiring, tests, packaging) with precise specs, file paths, acceptance criteria.
- **Reviewer — Opus 4.8** (`Agent`, `model: "opus"`). Audits each PR-sized change for
  correctness, security (no plaintext/logged secrets), COM/threading safety, ZZap payload
  correctness, RU-UI completeness, and PROJECT_MEMORY adherence.

Loop per task: **Architect specs → implement (self or Executor) → review → integrate + update
docs + commit.** Commit small reviewed increments (branch off `main`, then merge/push when the
user asks). Use plan mode for non-trivial phases.

## Work to do this session (in order)

### A. Live validation — Phase 1 + the first REAL ZZap POST (Architect-owned, STILL OPEN)
Needs the **user's real 1C credentials** and a **reachable cabinet-#1 base** (server
`192.168.55.34`, base `ut2025`, user `Натали` with the "Внешнее соединение" right). Secrets go
through **DPAPI — never plaintext on disk, never logged**; you never see the password (prompt
via `getpass` in a helper, or via the GUI password field). Two paths now exist — either is fine:
- **Via the GUI:** Подключение 1С → enter creds → «Проверить соединение»; open a cell → «Загрузить
  склады и виды цен из 1С» (confirm Склады hierarchical / ВидыЦен flat; fix `discover` if wrong);
  add a cell for template `330017019` + price type "ZZap" + the 3 sales warehouses; run it in
  **staging** first, then flip staging off and do **one real run** (confirm HTTP 200
  `{"success":true}` and the template was replaced).
- **Headless helper:** build `scripts/validate_1c.py` (getpass → store DPAPI → `test_connection`,
  verify friendly errors for wrong password / missing external-connection right, refine
  `describe_1c_error` against the real text; `discover`; `build_price_query` returns **~4154
  articles** matching the old CLI at `C:\Users\Admin\Desktop\zzap`).
Update `ROADMAP.md` Phase 1 → ✅ and the Phase 2 live-send → ✅ when validated.

### B. Phase 4 — Scheduler & background (system tray). Architect specs; implement; review.
**Model decided by the user (2026-06-23): APScheduler INSIDE the app + system tray — NOT the
Windows Task Scheduler.** Config-driven, RU UI, safety-first. **Reuse `CellRunner` — no business
logic in the scheduler/tray.**
- **APScheduler** interval job built from the `interval_hours` setting (default 5). Enable
  **coalescing + a misfire grace** so missed runs (PC asleep/off) fire once on resume (not a
  flood). Each tick runs `run_all_enabled` (or per-cell), plus a `retry_pending` flush pass.
- **Threading/DB:** every run opens its **own `Database`** on the worker (DAL is thread-affine;
  WAL makes it safe) — mirror `app/gui/screens/cells.py:_run_one`/`_run_all`. COM runs off the
  UI thread. Respect the **global staging kill-switch** and per-cell staging (CellRunner already
  enforces this; the scheduler must NOT bypass it).
- **System tray** (`QSystemTrayIcon`): run minimized to tray; menu (RU) = next run time, last
  results summary, «Запустить сейчас» (all / pick a cell), «Открыть окно», «Выход». Close-to-tray
  (not quit) with a first-time RU hint. Surface run outcomes as tray notifications (RU).
- **Offline recovery / catch-up after an outage** (power or internet cut → on resume everything
  uploads IMMEDIATELY — explicit user requirement):
  - *Missed scheduled runs* (PC was off/asleep): on startup AND each tick, compare the last
    successful upload time (state.json / `run_history`) against the interval; if overdue, run
    **immediately** (catch-up) instead of waiting for the next planned tick.
  - *Failed sends* (no network at upload time): the file is already staged to pending
    (`CellRunner.mark_pending`). A frequent **flush-pending** pass + retry-on-reconnect re-sends
    it **as soon as the network returns** (`RESEND_OK`).
  - *Offline "did it send?" check*: derive it from the cell's journal/state (`last_success`,
    presence of pending) — no ZZap round-trip. (Online published-row confirmation via
    `GET /stat/prices` stays optional, Phase 5.)
  - A light network-reachability check before a real POST (avoid spurious FAILs) + a periodic
    pending-retry timer.
- **Live interval changes:** when the user saves a new interval in Настройки, reschedule the job
  without restart. Reflect scheduler state (running / next run) in the UI.
- **Autostart with Windows (per-user):** wire the existing `autostart` setting to a per-user
  **HKCU\Software\Microsoft\Windows\CurrentVersion\Run** entry. Toggling it in Настройки
  adds/removes the entry; launch minimized to tray on autostart.
- **Tests:** unit-test schedule/reschedule, the offline catch-up decision (overdue → run now,
  driven by state/run_history), and autostart enable/disable — with timers/registry/scheduler
  mocked (no real timers, no live 1C/ZZap). Keep the suite green & offline.
**Exit:** the app uploads on schedule while minimized to tray; survives sleep/wake and a network
outage — missed and pending uploads go out immediately on resume; never publishes when staging is
on; honors interval changes live; starts with Windows.

### C. (Optional, if time) Phase 5 polish
Error toasts/notifications (RU), retry/pending UX, log rotation, ZZap 4xx → clear RU messages,
and the optional post-upload publish confirmation (`GET /api/client/v1/stat/prices`,
non-blocking) described in ROADMAP §5 Phase 5.

## Operating rules (unchanged)
- **RU UI everywhere** (see the warning block above).
- **Safety first:** new cells default to **staging**; real uploads only after explicit
  confirmation. Never publish silently. Honor the global staging kill-switch.
- **Secrets:** 1C password + ZZap keys encrypted at rest (DPAPI), masked in UI, never logged.
  Keep `error_text`'s `Pwd=`/`Usr=` redaction (incl. doubled-quote escaping).
- **Windows/COM correctness is non-negotiable:** 64-bit Python, per-thread
  CoInitialize/CoUninitialize, segfault-safe teardown, UTF-8 logging. Long 1C/network/CellRunner
  work off the UI thread (QThreadPool worker + queued UI-thread delivery via `AsyncRunner`).
- **ZZap payload is fixed:** `POST .../v1/price1c/upload`, header `zzap-api-key`, body
  `file_name, file_body(base64 xlsx), code_templ, part_num=0, part_total=1`. Do not "fix" it.
- **Duplicate rule:** `find_duplicate_risks` is the rule; the GUI banner surfaces it.
- **Tests:** unit-test with COM + HTTP (and now timers/registry) mocked. Don't require a live
  1C/ZZap to run the suite.
- Keep `PROJECT_MEMORY.md` and `ROADMAP.md` current. Commit each reviewed increment.

After Phase 4 lands and is reviewed, proceed to **Phase 5 (reliability/polish)** then **Phase 6
(packaging: PyInstaller exe + installer + first-run wizard)**.
