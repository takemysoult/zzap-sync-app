# Prompt for the next session (paste into a fresh Claude Code session)

> Run the **main session on Opus 4.8 with maximum thinking effort** ("extra"). The main
> session is the **Architect & Core Controller**. It delegates implementation to a
> **Sonnet 4.6** executor and code review to an **Opus 4.8** reviewer via the Agent tool.

---

You are a **senior software engineer** continuing a Windows desktop app that syncs 1C price
lists to ZZap. Work in `C:\Users\Admin\Desktop\zzap app`. **Phases 0 and 1 are already done,
reviewed, and committed** — do not rebuild them; continue the plan.

## Read first (do not skip)
1. `PROJECT_MEMORY.md` — verified facts (1C COM quirks, the WORKING ZZap API endpoint +
   payload, the data model, dedup/duplicate rules, the reusable engine). Ground truth.
2. `ROADMAP.md` — product definition, stack, architecture, phased plan, and per-phase status.
3. `README.md` — repo layout and current status.
Then skim the code: `engine/` (models, config, transform, zzap_client, delivery,
query_builder, sources/com.py `Com1C` + `ComPriceSource`), `app/db` (DAL + schema),
`app/security` (DPAPI), `app/services/connection.py` (`ConnectionManager`).

## Source of truth on the data source
**We use COM** (1C external connection) — confirmed. OData stays a future/secondary path
only; do not invest in it now.

## Current state (committed)
- **Phase 0** (`engine/`, `app/db` SQLite DAL, `app/security` DPAPI, tests, venv, git baseline).
- **Phase 1** (`ConnectionManager`: connect/test/discover with friendly RU errors; the dynamic
  `build_price_query` reproducing PROJECT_MEMORY §3; `Com1C` shared segfault-safe context).
- **52 tests pass** with no live 1C/ZZap (COM + HTTP behind mockable seams).
- 64-bit venv at `.venv` (Python 3.12 x64). Run tests: `.venv\Scripts\python -m pytest`.

## Roles & models (multi-agent workflow — keep using this)
- **Architect & Core Controller — Opus 4.8 (this main session, max thinking).**
  Owns architecture, the engine/core, COM correctness, the query builder, the ZZap payload,
  secrets, scheduler semantics, and the **duplicate-detection rule**. Personally implements
  and guards core-correctness code; breaks work into small specced tasks; integrates results.
  Does NOT hand off core-correctness decisions.
- **Executor — Sonnet 4.6** (`Agent` with `model: "sonnet"`).
  Implements specced tasks: GUI screens, DAL/boilerplate, file parsing, wiring, tests,
  packaging. Give it precise specs, file paths, and acceptance criteria; it returns code.
- **Reviewer — Opus 4.8** (`Agent` with `model: "opus"`).
  Audits each PR-sized change for correctness, security (no plaintext secrets, no secret in
  logs), COM/threading safety, ZZap payload correctness, and adherence to PROJECT_MEMORY.
  Returns findings; the Architect decides and routes fixes back to the Executor.

Loop per task: **Architect specs → Executor implements → Reviewer audits → Architect
integrates & updates docs & commits.** For pure core/engine work the Architect may implement
directly, but still run the Reviewer on it.

## Work to do this session (in order)

### A. Phase 1 live validation (needs the user's real 1C credentials, via COM)
Architect-owned. Using `ConnectionManager` against the real cabinet-#1 base:
1. `test_connection` succeeds; verify friendly errors for a wrong password and for a user
   WITHOUT the "Внешнее соединение" right; refine `describe_1c_error` against the real
   message text.
2. `discover` returns the real warehouse and price-type lists. **Confirm the catalog
   hierarchy assumption**: `Справочник.Склады` hierarchical (exclude groups) vs
   `Справочник.ВидыЦен` flat. If wrong, fix `build_catalog_names_query` usage (a flat catalog
   referencing `ЭтоГруппа` errors; a hierarchical one without the filter shows folders).
3. Run `build_price_query` for cabinet #1's known inputs (warehouses + price type "ZZap") and
   confirm the row count/content matches the old CLI (`C:\Users\Admin\Desktop\zzap`,
   ~4154 articles). Treat snapshot-time deltas as expected, not bugs.
Update `ROADMAP.md` Phase 1 to ✅ when validated. Secrets must be entered through the app's
DPAPI store — never written to disk in plaintext, never logged.

### B. Phase 2 — Cell engine (headless). Architect implements core; Reviewer audits.
1. **CellRunner**: run ONE cell end-to-end — build the query from (warehouses, price type) →
   `ComPriceSource.fetch_rows` → `clean_rows` → `apply_exclusions` → `build_xlsx`
   (include_header=False, the cell's columns) → upload via `upload_price` with the cabinet's
   API key and `code_templ` → record `run_history` + per-cell `Delivery` (journal/state).
   - **Safety:** respect staging — effective real upload only when **global staging is OFF
     AND the cell's `staging_mode` is OFF**; otherwise build the file and record `STAGED`,
     do NOT POST. New cells default to staging.
   - On upload failure, stage to pending and allow retry (per-cell `Delivery`).
2. **Multi-cell run** + per-cell `run_history`; a "run all enabled" path.
3. **Duplicate-across-warehouses detector** (PROJECT_MEMORY §4): warn when two enabled cells
   of the **same cabinet** can emit the same article (e.g. an item present on warehouses
   split across two templates). Architect owns this rule; surface a clear warning the GUI
   can show later.
4. **DAL thread-affinity** (Reviewer note): the scheduler will run cells on worker threads;
   open a `Database` per worker thread (or `check_same_thread=False` + a lock). Decide and
   implement so Phase 4 is safe.

### C. NEW feature — import cell exclusions from an Excel file (`.xlsx` / `.xls`)
Architect specs the normalization contract; **Executor implements the file reader + DAL
wiring + tests**; Reviewer audits parsing/security.
- **Engine (pure, testable):** add `engine/exclusions.py` (or extend `transform.py`) with
  `read_exclusion_articles(path, *, column=0, skip_header=False) -> list[str]`:
  - `.xlsx`/`.xlsm` → openpyxl in **read_only** mode (no formula evaluation).
  - `.xls` → **xlrd>=2.0.1** (add to `requirements.txt`). xlrd 2.x reads only `.xls`.
  - else → `ValueError` with a friendly message.
  - Return the non-empty cell values of the chosen column as strings (one per article),
    skipping the header row if asked. Normalization (trim+upper, dedupe) is reused from the
    existing exclusion mechanism; the stored `exclusion_list.articles` keeps one article per
    line, and `apply_exclusions` already normalizes at run time.
- **DAL:** add a helper to create/replace/append an `exclusion_list` from imported articles
  and assign it to a cell (`cell.exclusion_list_id`).
- **Acceptance:** unit tests build a small `.xlsx` fixture (and, if practical, a tiny `.xls`
  or a mocked xlrd) and assert articles are read from the chosen column, header skipped,
  blanks dropped, and that a cell with the imported list actually filters those articles in
  a CellRunner run. Big files: stream with read_only; don't load the whole workbook eagerly.
- The GUI button ("Импорт исключений из Excel") is Phase 3; this session delivers the
  headless function + DAL + tests so the GUI just calls it.

### Phase 2 carry-over hardening (Reviewer notes, fold into B)
- Add an end-to-end test that a cabinet's stored `api_url` flows into `ZzapConfig` and
  `upload_price` POSTs to it (guards against a wrong-host upload).
- If DPAPI entropy is ever added, the DAL must persist/derive it consistently.

## Operating rules (unchanged)
- **Safety first:** new cells default to **staging**; real uploads only after explicit
  confirmation. Never publish silently.
- **Secrets:** 1C password + ZZap keys encrypted at rest (DPAPI), masked in UI, never logged.
  `ConnectionManager.error_text` already redacts `Pwd=`/`Usr=`; keep it that way.
- **Windows/COM correctness is non-negotiable:** 64-bit Python, per-thread
  CoInitialize/CoUninitialize, the segfault-safe teardown (now in `Com1C.__exit__`),
  UTF-8 logging. Long 1C/network work off the UI thread.
- **ZZap payload is fixed:** `POST .../v1/price1c/upload`, header `zzap-api-key`, body
  `file_name, file_body(base64 xlsx), code_templ, part_num=0, part_total=1`. Do not "fix" it.
- **Duplicate rule:** warn before a config can place the same goods into two templates of the
  same cabinet (PROJECT_MEMORY §4).
- **Tests:** unit-test the engine and services with COM + HTTP mocked. Don't require a live
  1C/ZZap to run the suite.
- Keep `PROJECT_MEMORY.md` and `ROADMAP.md` current. Commit each reviewed increment (small,
  reviewed commits; you may branch off `main`). Use plan mode for non-trivial phases.

After Phase 2 + the Excel-import feature land and are reviewed, proceed to **Phase 3 (GUI)**,
delegating screen implementation to the Sonnet executor under your specs.
