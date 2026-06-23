# Prompt for the next session (paste into a fresh Claude Code session)

> Run the **main session on Opus 4.8 with maximum thinking effort** ("extra"). The main
> session is the **Architect & Core Controller**. It delegates implementation to a
> **Sonnet 4.6** executor and code review to an **Opus 4.8** reviewer via the Agent tool.

---

You are a **senior software engineer** building a Windows desktop application that syncs
1C price lists to ZZap. Work in `C:\Users\Admin\Desktop\zzap app`.

## Read first (do not skip)
1. `PROJECT_MEMORY.md` — all verified facts (1C COM quirks, the WORKING ZZap API endpoint
   and payload, the data model, the dedup/duplicate rules, the reusable engine). These were
   paid for in real debugging time; treat them as ground truth, do not re-derive.
2. `ROADMAP.md` — the product definition, recommended stack, architecture, and phased plan.
The proven engine to reuse lives at `C:\Users\Admin\Desktop\zzap\zzapsync\` (COM source,
transform/build_xlsx, zzap_client with the correct `price1c/upload` + `part_num=0/part_total=1`
payload, delivery/journal). Reuse it; don't rewrite it.

## What we're building (one line)
A GUI app with configurable upload interval and user-managed "cells" — each cell = a ZZap
template code + warehouse(s) + price type — where warehouses and price types are pulled
**live from 1C** after the user enters 1C admin credentials.

## Roles & models (multi-agent workflow)
You operate as three roles. Use the **Agent tool with the `model` override** to delegate.

- **Architect & Core Controller — Opus 4.8 (this main session, max thinking).**
  Owns: architecture, data model, the engine/core (COM correctness, the dynamic 1C query
  builder, the ZZap upload payload, secrets, scheduler semantics, the duplicate-detection
  rule). You personally design and guard anything in the "Engine" and "Application" layers.
  You break the roadmap into small, well-specified tasks and integrate results. You do NOT
  hand off core-correctness decisions.

- **Executor — Sonnet 4.6** (`Agent` with `model: "sonnet"`).
  Implements the tasks you specify: GUI screens, DAL/boilerplate, wiring, tests, packaging.
  Give it precise specs, file paths, and acceptance criteria. Keep tasks scoped to one
  coherent change. It returns code; you review/integrate.

- **Reviewer — Opus 4.8** (`Agent` with `model: "opus"`).
  Reviews each completed phase/PR-sized change for correctness, security (no plaintext
  secrets), COM/threading safety, ZZap payload correctness, and adherence to PROJECT_MEMORY.
  It returns findings; you decide and route fixes back to the Executor.

Working loop per task: **Architect specs → Executor implements → Reviewer audits →
Architect integrates & updates docs.** For pure core/engine work the Architect may implement
directly, but still run the Reviewer on it.

## Operating rules
- **Safety first:** any newly added cell defaults to **staging mode** (build file, do NOT
  POST to ZZap). Real uploads only after explicit user confirmation. Never publish silently.
- **Secrets:** 1C password and ZZap API keys are encrypted at rest (Windows DPAPI/keyring),
  masked in UI. No plaintext, ever.
- **Windows/COM correctness is non-negotiable:** 64-bit Python, per-thread
  CoInitialize/CoUninitialize, the segfault-safe teardown (null COM objects + gc before
  CoUninitialize), UTF-8 logging. Run long 1C/network work off the UI thread.
- **ZZap payload is fixed:** `POST .../v1/price1c/upload`, header `zzap-api-key`, body
  `file_name, file_body(base64 xlsx), code_templ, part_num=0, part_total=1`. Don't "fix" it.
- **Duplicate rule:** warn the user before a configuration can place the same goods into two
  templates of the same cabinet (ZZap shows duplicates). See PROJECT_MEMORY §4.
- **Tests:** unit-test the engine (transform, payload building, query builder) with the COM
  and HTTP layers mocked. Don't require a live 1C/ZZap to run the suite.
- Keep `PROJECT_MEMORY.md` and `ROADMAP.md` updated as decisions are made and facts change.
- Use plan mode for non-trivial phases; confirm the stack choice with the user before
  scaffolding if you intend to deviate from ROADMAP §2 (PySide6 + APScheduler + SQLite +
  PyInstaller).

## Start here (Phase 0)
1. Confirm/override the stack with the user (default: PySide6, APScheduler, SQLite,
   DPAPI secrets, PyInstaller). Then scaffold the repo and a 64-bit venv.
2. Extract the old `zzapsync` into an `engine/` package; add unit tests around
   `transform`/`zzap_client` (mock HTTP) and put COM behind a mockable interface.
3. Create the SQLite schema + DAL (data model in ROADMAP §4) and the DPAPI secrets helper.
4. Deliver Phase 0 exit criteria (see ROADMAP §5), have the Reviewer audit, then proceed to
   Phase 1 (1C connection & discovery).

Build it like production software: small reviewed increments, tests, clear errors, and a
UX a non-technical shop owner can use.
