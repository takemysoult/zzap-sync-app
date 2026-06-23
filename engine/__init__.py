"""Reusable, UI-agnostic engine for the ZZap Sync app.

Extracted from the proven CLI (`zzapsync`). Responsibilities:
  - sources: read price rows from 1C (COM today, OData later) behind a mockable interface
  - transform: clean rows, apply exclusions, build the 5-column XLSX
  - zzap_client: upload the XLSX to ZZap (price1c/upload, part_num=0/part_total=1)
  - delivery: per-target journal / state / pending-retry

Design rule: this package is pure and unit-testable. COM and HTTP are behind
injectable seams so the test suite runs without a live 1C or ZZap.
"""
__version__ = "0.1.0"
