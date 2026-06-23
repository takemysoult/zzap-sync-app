"""ZZap Sync desktop application package.

Layers (see ROADMAP.md §3):
  - app.db        — SQLite store + DAL (cells, cabinets, connections, settings, history)
  - app.security  — DPAPI-encrypted secret storage
  - (later) app.services — CellRunner, Scheduler, ConnectionManager
  - (later) app.ui        — PySide6 screens
"""
__version__ = "0.1.0"
