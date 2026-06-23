"""Сборка XLSX-файла для загрузки в ZZap.

Колонки расставляются по номерам из настроек (OutputConfig col_*), чтобы файл
точно лёг под соответствие колонок в шаблоне zzap. По умолчанию — 5 колонок
подряд: Производитель | Номер | Наименование | Количество | Цена.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook

from .models import PriceRow

log = logging.getLogger(__name__)

FIELD_TITLES = {
    "producer": "Производитель",
    "number": "Номер",
    "name": "Наименование",
    "quantity": "Количество",
    "price": "Цена",
}
DEFAULT_COLUMNS = {"producer": 1, "number": 2, "name": 3, "quantity": 4, "price": 5}


def clean_rows(rows: list[PriceRow]) -> list[PriceRow]:
    """Отбрасываем позиции без артикула (номера) — их zzap не сопоставит."""
    cleaned = [r for r in rows if r.number]
    skipped = len(rows) - len(cleaned)
    if skipped:
        log.warning("Пропущено позиций без номера (артикула): %d", skipped)
    return cleaned


def _norm_article(value: str) -> str:
    """Нормализация артикула для сравнения: trim + верхний регистр.

    Весь механизм исключений (импорт через normalize_articles и сопоставление в
    apply_exclusions) использует ОДНУ И ТУ ЖЕ функцию, поэтому import-time и
    run-time нормализация всегда совпадают (для не-ASCII upper()≠casefold(), но
    важно лишь согласованное применение по обе стороны)."""
    return (value or "").strip().upper()


def load_exclusions(path: str | Path | None) -> set[str]:
    """Читает файл исключений (по артикулу на строку; # — комментарий).

    Возвращает множество нормализованных артикулов. Если файла нет или путь
    пуст — пустое множество (фильтр просто ничего не отбросит).
    """
    if not path:
        return set()
    path = Path(path)
    if not path.exists():
        log.info("Файл исключений не найден (%s) — исключений нет.", path)
        return set()
    return parse_exclusions(path.read_text(encoding="utf-8"))


def parse_exclusions(text: str) -> set[str]:
    """Разбирает текст списка исключений (артикул на строку; # — комментарий).

    Вынесено отдельно, чтобы приложение могло хранить списки исключений в своём
    хранилище (а не только в файле) и переиспользовать ту же нормализацию.
    """
    excluded: set[str] = set()
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        excluded.add(_norm_article(line))
    return excluded


def normalize_articles(values: Iterable[object]) -> list[str]:
    """Нормализует список артикулов: trim+верхний регистр, без пустых, без дублей.

    В отличие от parse_exclusions (множество, для сравнения на лету) — возвращает
    УПОРЯДОЧЕННЫЙ список без повторов, пригодный для хранения в exclusion_list
    (по артикулу на строку). Используется при импорте исключений из Excel: тот же
    самый контракт нормализации, что и в остальном механизме исключений.
    """
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        article = _norm_article(v if isinstance(v, str) else "" if v is None else str(v))
        if article and article not in seen:
            seen.add(article)
            out.append(article)
    return out


def apply_exclusions(rows: list[PriceRow], excluded: set[str]) -> list[PriceRow]:
    """Убирает из выгрузки позиции, чей артикул есть в списке исключений."""
    if not excluded:
        return rows
    kept = [r for r in rows if _norm_article(r.number) not in excluded]
    removed = len(rows) - len(kept)
    log.info("Исключено из выгрузки по списку: %d позиций (осталось %d)", removed, len(kept))
    return kept


def build_xlsx(rows: list[PriceRow], path: str | Path, include_header: bool = True,
               columns: dict[str, int] | None = None) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = columns or DEFAULT_COLUMNS
    width = max(columns.values())

    wb = Workbook()
    ws = wb.active
    ws.title = "price"

    if include_header:
        header = [None] * width
        for field, col in columns.items():
            header[col - 1] = FIELD_TITLES[field]
        ws.append(header)

    for r in rows:
        line = [None] * width
        line[columns["producer"] - 1] = r.producer
        line[columns["number"] - 1] = r.number
        line[columns["name"] - 1] = r.name
        line[columns["quantity"] - 1] = r.quantity
        line[columns["price"] - 1] = r.price
        ws.append(line)

    try:
        wb.save(path)
    except PermissionError:
        # Файл занят (открыт в Excel и т.п.) — сохраняем под временным именем,
        # чтобы автозапуск не падал.
        alt = path.with_name(f"{path.stem}_{datetime.now():%Y%m%d_%H%M%S}{path.suffix}")
        log.warning("Файл %s занят (открыт?). Сохраняю как %s", path, alt)
        wb.save(alt)
        path = alt

    log.info("Сформирован файл: %s (строк данных: %d, колонок: %d)", path, len(rows), width)
    return path
