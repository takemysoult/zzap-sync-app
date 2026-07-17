"""Per-user application data locations (SQLite store + per-cell work files).

The GUI and (later, Phase 4) the background scheduler both need a stable place to
keep the database and each cell's working directory (built XLSX, journal, pending).
On Windows that is ``%LOCALAPPDATA%\\<flavor dir>`` (``ZZapSync`` or ``PriceMailer``
— the two flavors are separate apps with separate data). Set ``ZZAP_APP_DATA`` to
override the whole location (tests use this to isolate state).
"""
from __future__ import annotations

import os
from pathlib import Path

from . import flavor


def app_data_dir() -> Path:
    override = os.environ.get("ZZAP_APP_DATA")
    if override:
        return Path(override)
    dirname = flavor.app_id()
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if base:
        return Path(base) / dirname
    return Path.home() / f".{dirname.lower()}"


def db_path() -> Path:
    return app_data_dir() / flavor.db_filename()


def work_dir() -> Path:
    """Root for per-cell working dirs (CellRunner writes cell_<id>/ under here)."""
    return app_data_dir() / "work"


def logs_dir() -> Path:
    """Directory for the rotating application log (Phase 5)."""
    return app_data_dir() / "logs"


def heartbeat_path() -> Path:
    """File the running app touches periodically so the external watchdog can tell it
    is alive (Phase 6)."""
    return app_data_dir() / "heartbeat.txt"


def ensure_dirs() -> None:
    work_dir().mkdir(parents=True, exist_ok=True)
    logs_dir().mkdir(parents=True, exist_ok=True)
