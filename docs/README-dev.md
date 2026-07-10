# ZZap Sync App

A Windows desktop app that syncs 1C price lists to ZZap on a schedule. It replaces the
old `config.ini` + Task Scheduler CLI with a GUI built around configurable **upload cells**
— each cell maps **(warehouse(s) + price type)** from 1C to a **ZZap template (`code_templ`)**.

> **Start here:** `PROJECT_MEMORY.md` (verified facts — 1C COM, the working ZZap API,
> the data model) and `ROADMAP.md` (product definition + phased plan). These are ground truth.

## Stack
Python 3.10 (64-bit, to match 1C x64) · PySide2 (Qt5 — runs on Win10 1607, where the
client PC lives; Qt6 needs 1809+; Python 3.10 is PySide2's ceiling) · APScheduler ·
SQLite · Windows DPAPI for secrets · PyInstaller for packaging.

## Layout
```
engine/         Pure, UI-agnostic, unit-tested engine (extracted from the proven CLI)
  sources/        PriceSource interface + ComPriceSource (1C COM), OdataPriceSource
  transform.py    clean rows / exclusions / build the 5-column XLSX
  zzap_client.py  upload to ZZap (price1c/upload, part_num=0/part_total=1) — DO NOT change payload
  delivery.py     per-cell journal / state / pending-retry
app/            Application layer
  db/             SQLite schema + DAL (cells, cabinets, connections, settings, history)
  security/       DPAPI-encrypted secret storage
  services/       ConnectionManager, CellRunner, duplicate detector
  gui/            PySide2 desktop UI (screens drive the services; no business logic)
  paths.py        Per-user data locations (DB + per-cell work dir under %LOCALAPPDATA%)
tests/          pytest suite (engine + DAL + secrets + GUI smoke; COM and HTTP mocked)
reference/      Legacy CLI blueprints for Phase 1 (not part of the app)
```

## Develop
```powershell
py -3.10 -m venv .venv310
.venv310\Scripts\python -m pip install -r requirements-dev.txt
.venv310\Scripts\python -m pytest
```

The test suite runs without a live 1C or ZZap (COM and HTTP are behind mockable seams).

Run the desktop app (from the 64-bit venv):
```powershell
.venv310\Scripts\python -m app.gui
```

## Safety
- New cells default to **disabled**; a manual «Запустить» asks for confirmation, and a build that
  yields **0 rows is refused** (a ZZap upload fully replaces the template — never wipe it with an
  empty file). A **watchdog** restarts the app and notifies the user if it stops running.
- Secrets (1C password, ZZap API keys) are **encrypted at rest** (DPAPI) and masked in the UI.
  Never committed; never logged.

## Status
- **Phase 0 (Foundations) — complete.** Engine extracted + tested, SQLite DAL, DPAPI secrets.
- **Phase 1 (1C connection & discovery) — code complete (mocked-COM).** ConnectionManager,
  live warehouse/price-type discovery, and the dynamic 5-column query builder, all unit-tested.
  Live validation against a real 1C base is the remaining Phase 1 sign-off step.
- **Phase 2 (Cell engine, headless) — code complete (mocked COM/HTTP).** `CellRunner` runs a
  cell end-to-end with a 0-row safety guard + pending/retry, the duplicate-across-warehouses
  detector, Excel exclusions import (`.xlsx`/`.xls`), and DAL thread-affinity (WAL). The first
  real ZZap upload is exercised together with Phase 1 live validation.
- **Phase 3 (GUI) — code complete.** PySide2 desktop app (`app/gui/`; built on PySide6,
  later ported to PySide2 for Win10 1607): connection, cabinets,
  cells (with live 1C dropdowns, exclusions import, duplicate banner, per-template checklist),
  settings, and status/journal screens. All COM/network/CellRunner work runs off the UI thread
  (a per-thread `Database` in workers); secrets are masked and never reloaded into fields.
- **Phase 4 (Scheduler & system tray) — code complete.** APScheduler runs *inside* the app
  (`app/services/scheduler.py`): an interval job (every N hours) runs all enabled cells, a frequent
  pending-flush pass re-sends files staged while offline, and **offline recovery** is two-fold —
  missed runs catch up immediately on resume (`coalesce` + a startup overdue check), and pending
  files flush as soon as the network returns. The app runs minimized to the **system tray**
  (`QSystemTrayIcon`, RU menu + notifications), starts with Windows (per-user `HKCU\...\Run`), and
  reschedules live when the interval changes. A `threading.Lock` serialises every run. Scheduled
  uploads pair with Phase 1/2 live validation for sign-off.
- **Phase 5 (Reliability & polish) — code complete.** ZZap HTTP errors map to clear RU messages and
  are classified permanent (bad key/URL/content → ERROR, no endless retry) vs transient (5xx/429/
  timeout → queued for retry). A **0-row safety guard** refuses an upload that would wipe the
  template. App logs rotate under `%LOCALAPPDATA%\ZZapSync\logs\`. Pending uploads are visible
  («Ожидает досылки») with a manual «Дослать отложенное» action. The optional publish confirmation
  (`GET /stat/prices`) is deferred to live validation (its response shape needs the real cabinet).
- **Phase 6 (Packaging & hardening) — done; released.** The dev-only staging mode is **removed**
  (the app uploads for real; the 0-row guard remains). A **single-instance** guard stops a second
  copy (it just raises the open window). A **crash watchdog** (a Windows scheduled task driven by a
  heartbeat) restarts the app and notifies the user if it stops — toggleable in Настройки. Packaged
  with **PyInstaller + Inno Setup** into `ZZapSync-Setup-1.0.0.exe` (Program Files install, Start-Menu
  shortcut, autostart, uninstall cleanup), published to GitHub Releases `v1.0.0`.

118 tests pass. See `ROADMAP.md` §5 for the full phase plan. Live 1C is verified read-only; the
first real ZZap upload happens when you enable a cell in the installed app.
