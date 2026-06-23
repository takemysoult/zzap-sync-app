"""Единый формат строки прайс-листа для zzap."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PriceRow:
    """Одна позиция прайса в порядке колонок шаблона zzap."""
    producer: str    # Производитель
    number: str      # Номер (артикул)
    name: str        # Наименование
    quantity: float  # Количество
    price: float     # Цена
