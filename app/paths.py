"""Per-user application data locations (SQLite store + per-cell work files).

The GUI and (later, Phase 4) the background scheduler both need a stable place to
keep the database and each cell's working directory (built XLSX, journal, pending).
On Windows that is ``%LOCALAPPDATA%\\ZZapSync``. Set ``ZZAP_APP_DATA`` to override
the whole location (tests use this to isolate state).
"""
from __future__ import annotations

import os
from pathlib import Path

_APP_DIRNAME = "ZZapSync"


def app_data_dir() -> Path:
    override = os.environ.get("ZZAP_APP_DATA")
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if base:
        return Path(base) / _APP_DIRNAME
    return Path.home() / f".{_APP_DIRNAME.lower()}"


def db_path() -> Path:
    return app_data_dir() / "zzapsync.db"


def work_dir() -> Path:
    """Root for per-cell working dirs (CellRunner writes cell_<id>/ under here)."""
    return app_data_dir() / "work"


def logs_dir() -> Path:
    """Directory for the rotating application log (Phase 5)."""
    return app_data_dir() / "logs"


def ensure_dirs() -> None:
    work_dir().mkdir(parents=True, exist_ok=True)
    logs_dir().mkdir(parents=True, exist_ok=True)
