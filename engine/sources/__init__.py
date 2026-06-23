"""Price-row sources for the engine, behind a common mockable interface.

`PriceSource` is the seam that keeps the engine testable without a live 1C:
  - ComPriceSource — 1C external connection (COM) on Windows
  - OdataPriceSource — OData (future / alternate base)
  - test fakes — return canned rows
"""
from .base import PriceSource

__all__ = ["PriceSource"]
