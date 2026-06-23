"""The price-source interface.

Anything that can produce price rows for one cell implements `fetch_rows()`.
Keeping COM/HTTP behind this Protocol lets CellRunner and the engine be unit-tested
with a fake source — no live 1C/ZZap required.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..models import PriceRow


@runtime_checkable
class PriceSource(Protocol):
    def fetch_rows(self) -> list[PriceRow]:
        """Return the price rows for this source's configured query.

        Must yield rows in ZZap column order (producer, number, name, quantity, price).
        Raises a RuntimeError/ValueError with a human-readable message on failure.
        """
        ...
