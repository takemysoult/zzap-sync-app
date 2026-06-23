# reference/ — legacy blueprints (not part of the app)

These are scripts from the original CLI (`C:\Users\Admin\Desktop\zzap`), kept as
**read-only blueprints** for upcoming phases. They are NOT imported by the app or
the test suite and are not run in CI.

- `check_com.py` — minimal 1C COM connection + query + 1C-error parsing.
  Blueprint for the **ConnectionManager** (Phase 1). Original read `config.ini`;
  the app reads connection params from SQLite/DPAPI instead.
- `explore_1c.py` — discovers warehouses (`Справочник.Склады`) and price types
  (`Справочник.ВидыЦен`). Blueprint for the app's **live dropdowns** (Phase 1).
- `register_typelib.py` — registers the 1C COM connector typelib in HKCU (per-user),
  fixing "Библиотека не зарегистрирована". Standalone utility; still usable as-is
  when a machine needs the connector registered once.

The proven engine these scripts belonged to has been extracted (and unit-tested)
into the top-level `engine/` package. See `SEED_NOTES.md`.
