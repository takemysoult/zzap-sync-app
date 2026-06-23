"""Application/service layer: orchestrates use-cases over the engine + data store.

  - ConnectionManager — connect to 1C (COM), validate, discover warehouses/price types
  - (later) CellRunner — run one cell end-to-end
  - (later) Scheduler — APScheduler interval jobs
"""
from .connection import ConnectionManager, ConnectionResult, Discovery

__all__ = ["ConnectionManager", "ConnectionResult", "Discovery"]
