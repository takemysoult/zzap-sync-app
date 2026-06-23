# Prompt for the next session (paste into a fresh Claude Code session)

> Run the **main session on Opus 4.8 with maximum thinking effort** ("extra"). The main
> session is the **Architect & Core Controller**. It delegates implementation to a
> **Sonnet 4.6** executor and code review to an **Opus 4.8** reviewer via the Agent tool.
> If delegation is unavailable (rate limit, etc.), the Architect implements directly — but
> still runs the Reviewer on the result.

---

You are a **senior software engineer** continuing a Windows desktop app that syncs 1C price
lists to ZZap. Work in `C:\Users\Admin\Desktop\zzap app`. **Phases 0, 1, and 2 are done,
reviewed, and committed + pushed to `main`** — do not rebuild them; continue the plan.

## Read first (do not skip)
1. `PROJECT_MEMORY.md` — verified facts (1C COM quirks, the WORKING ZZap API endpoint +
   payload, the data model, dedup/duplicate rules, the reusable engine, and the Phase 2
   decisions). Ground truth.
2. `ROADMAP.md` — product definition, stack, architecture, phased plan, and per-phase status.
3. `README.md` — repo layout and current status.
Then skim the code: `engine/` (models, config, `transform` incl. `normalize_articles`,
`query_builder`, `exclusions`, `zzap_client`, `delivery`, `sources/com.py` `Com1C` +
`ComPriceSource`), `app/db` (DAL + schema), `app/security` (DPAPI), `app/services`
(`connection.py` `ConnectionManager`, `cell_runner.py` `CellRunner`, `duplicates.py`).

## Source of truth on the data source
**We use COM** (1C external connection) — confirmed. OData stays a future/secondary path
only; do not invest in it now.

## Current state (committed + pushed to `main`)
- **Phase 0** — `engine/`, SQLite DAL, DPAPI secrets, tests, venv, git baseline.
- **Phase 1** (code-complete, mocked COM) — `ConnectionManager` (connect/test/discover,
  friendly RU errors via `describe_1c_error`, `error_text` redacts `Pwd=`/`Usr=`),
  `build_price_query` (PROJECT_MEMORY §3), `Com1C` segfault-safe context. **Live validation
  still pending** (needs real creds).
- **Phase 2** (code-complete, headless, mocked COM/HTTP, reviewed SHIP-WITH-FIXES → fixed):
  - `CellRunner` (`app/services/cell_runner.py`) — runs one cell end-to-end. **Staging gate:**
    a real POST only when the global `staging_mode` setting is OFF **and** the cell's
    `staging_mode` is OFF; else `STAGED`. Statuses `OK`/`STAGED`/`FAIL`/`ERROR`/`RESEND_OK`;
    `FAIL` stages to pending and `retry_pending` re-sends; `run_all_enabled` isolates errors;
    secrets redacted on every error path. Source + uploader are injected (testable).
  - `find_duplicate_risks` (`app/services/duplicates.py`) — the §4 duplicate-across-warehouses
    detector (same cabinet, different `code_templ`; shared warehouse = high confidence).
  - **Excel exclusions import** — `engine/exclusions.py:read_exclusion_articles`
    (.xlsx/.xlsm via openpyxl read-only, .xls via xlrd≥2.0.1) + DAL `import_exclusion_list` /
    `assign_exclusion_list_to_cell` + `engine.transform.normalize_articles`.
  - **DAL thread-affinity** — per-thread `Database` + WAL + `busy_timeout` (file DBs only).
- **78 tests pass** with no live 1C/ZZap (COM + HTTP behind mockable seams).
- 64-bit venv at `.venv` (Python 3.12 x64). Run tests: `.venv\Scripts\python -m pytest`.

## Roles & models (multi-agent workflow — keep using this)
- **Architect & Core Controller — Opus 4.8 (this main session, max thinking).** Owns
  architecture, the engine/core, COM correctness, the query builder, the ZZap payload,
  secrets, scheduler semantics, the duplicate rule, and the **live-validation steps**.
  Implements core-correctness code itself; breaks the rest into small specced tasks.
- **Executor — Sonnet 4.6** (`Agent`, `model: "sonnet"`). Implements specced tasks: GUI
  screens, DAL/boilerplate, wiring, tests, packaging. Give precise specs, file paths, and
  acceptance criteria.
- **Reviewer — Opus 4.8** (`Agent`, `model: "opus"`). Audits each PR-sized change for
  correctness, security (no plaintext secrets, no secret in logs), COM/threading safety,
  ZZap payload correctness, and PROJECT_MEMORY adherence. Returns findings; the Architect
  decides and routes fixes.

Loop per task: **Architect specs → Executor implements → Reviewer audits → Architect
integrates & updates docs & commits.** Commit small reviewed increments on a branch off
`main`; use plan mode for non-trivial phases.

## Work to do this session (in order)

### A. Live validation — Phase 1 + the first REAL ZZap POST (Architect-owned)
Needs the **user's real 1C credentials** and a **reachable cabinet-#1 base** (server
`192.168.55.34`, base `ut2025`, user `Натали` with the "Внешнее соединение" right). Secrets
go through **DPAPI — never plaintext on disk, never logged**; you never see the password
(prompt via `getpass` in a helper the user runs).
1. Build a small headless helper (e.g. `scripts/validate_1c.py`): `getpass` the 1C password,
   store it DPAPI-encrypted in the SQLite store as the cabinet-#1 `Connection1C`, then:
   - `test_connection` succeeds; verify the friendly error for a wrong password and for a
     user WITHOUT the external-connection right; refine `describe_1c_error` against the real
     message text.
   - `discover` returns the real warehouse + price-type lists. **Confirm catalog hierarchy:**
     `Справочник.Склады` hierarchical (exclude groups) vs `Справочник.ВидыЦен` flat. Fix the
     `build_catalog_names_query` usage if wrong.
   - `build_price_query` for cabinet #1 (the 3 sales warehouses + price type "ZZap") returns
     **~4154 articles** matching the old CLI (`C:\Users\Admin\Desktop\zzap`). Snapshot-time
     deltas are expected, not bugs.
2. **First real upload:** configure ONE cell for template `330017019` with the cabinet's ZZap
   API key (DPAPI). Run it in **staging first** (build only), then flip the cell's staging
   OFF (global staging OFF) and do **one real `CellRunner` upload**; confirm HTTP 200
   `{"success":true}` and that the template was replaced. This signs off the live-send path.
Update `ROADMAP.md` Phase 1 → ✅ and the Phase 2 live-send → ✅ when validated.

### B. Phase 3 — GUI (PySide6). Architect specs screens; Executor implements; Reviewer audits.
Config + monitoring only — **no business logic in the GUI**; call the existing services.
- **Connection screen:** enter 1C creds, "Test connection" (`ConnectionManager.test_connection`
  off the UI thread), save encrypted (DPAPI).
- **Cabinets screen:** name + ZZap API key (masked) + `api_url`; store encrypted.
- **Cells table:** add/edit/delete; warehouse & price-type dropdowns populated live from 1C
  (`ConnectionManager.discover`); `code_templ` input; per-cell **enabled** + **staging**
  toggles; exclusions editor with an **"Импорт исключений из Excel"** button that calls
  `read_exclusion_articles` → `db.import_exclusion_list` → `db.assign_exclusion_list_to_cell`
  (column picker + skip-header checkbox); per-template checklist reminder (PROJECT_MEMORY §5).
- **Duplicate warning banner** from `find_duplicate_risks` (red = shared warehouse, amber =
  split).
- **Settings:** interval hours, the GLOBAL staging kill-switch, autostart.
- **Status/logs view:** `run_history` table + journal tail; **"Run now"** (one cell / all
  enabled) via `CellRunner` on a worker thread (its own `Database` — WAL makes this safe).
- **Threading:** all COM/network/`CellRunner` work off the UI thread (QThread worker +
  signals); the GUI never blocks. New cells default to staging in the UI too.
**Exit:** a non-technical user can configure everything and run a cell from the UI without
touching files.

## Operating rules (unchanged)
- **Safety first:** new cells default to **staging**; real uploads only after explicit
  confirmation. Never publish silently.
- **Secrets:** 1C password + ZZap keys encrypted at rest (DPAPI), masked in UI, never logged.
  Keep `error_text`'s `Pwd=`/`Usr=` redaction (incl. doubled-quote escaping).
- **Windows/COM correctness is non-negotiable:** 64-bit Python, per-thread
  CoInitialize/CoUninitialize, segfault-safe teardown (in `Com1C.__exit__`), UTF-8 logging.
  Long 1C/network work off the UI thread.
- **ZZap payload is fixed:** `POST .../v1/price1c/upload`, header `zzap-api-key`, body
  `file_name, file_body(base64 xlsx), code_templ, part_num=0, part_total=1`. Do not "fix" it.
- **Duplicate rule:** warn before a config can place the same goods into two templates of the
  same cabinet (PROJECT_MEMORY §4) — `find_duplicate_risks` is the rule; surface it in the UI.
- **Tests:** unit-test engine + services with COM + HTTP mocked. Don't require a live
  1C/ZZap to run the suite.
- Keep `PROJECT_MEMORY.md` and `ROADMAP.md` current. Commit each reviewed increment.

After Phase 3 lands and is reviewed, proceed to **Phase 4 (scheduler + system tray)**.
