# ZZap Sync App

A Windows desktop app that syncs 1C price lists to ZZap on a schedule. It replaces the
old `config.ini` + Task Scheduler CLI with a GUI built around configurable **upload cells**
— each cell maps **(warehouse(s) + price type)** from 1C to a **ZZap template (`code_templ`)**.

> **Start here:** `PROJECT_MEMORY.md` (verified facts — 1C COM, the working ZZap API,
> the data model) and `ROADMAP.md` (product definition + phased plan). These are ground truth.

## Stack
Python 3.12 (64-bit, to match 1C x64) · PySide6 · APScheduler · SQLite · Windows DPAPI
for secrets · PyInstaller for packaging.

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
tests/          pytest suite (engine + DAL + secrets; COM and HTTP mocked)
reference/      Legacy CLI blueprints for Phase 1 (not part of the app)
```

## Develop
```powershell
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest
```

The test suite runs without a live 1C or ZZap (COM and HTTP are behind mockable seams).

## Safety
- New cells default to **staging** (build the XLSX, do NOT POST). Real uploads require an
  explicit per-cell opt-out, plus the global staging kill-switch being off.
- Secrets (1C password, ZZap API keys) are **encrypted at rest** (DPAPI) and masked in the UI.
  Never committed; never logged.

## Status
- **Phase 0 (Foundations) — complete.** Engine extracted + tested, SQLite DAL, DPAPI secrets.
- **Phase 1 (1C connection & discovery) — code complete (mocked-COM).** ConnectionManager,
  live warehouse/price-type discovery, and the dynamic 5-column query builder, all unit-tested.
  Live validation against a real 1C base is the remaining Phase 1 sign-off step.

52 tests pass. See `ROADMAP.md` §5 for the full phase plan. Next: Phase 2 (cell engine).
