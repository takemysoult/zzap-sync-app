"""PySide2 desktop GUI (Phase 3; ported from PySide6 for Win10 1607 support).

Config + monitoring only — the GUI holds NO business logic. Every screen drives the
existing service layer (ConnectionManager, CellRunner, the duplicate detector, the
Excel exclusions import). All blocking work (1C COM, ZZap HTTP, CellRunner) runs off
the Qt UI thread via :mod:`app.gui.workers`; worker callables open their own
``Database`` because the DAL is thread-affine (see app.db.dal).
"""
