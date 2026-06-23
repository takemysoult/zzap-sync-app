"""Чтение списка артикулов-исключений из Excel-файлов.

Поддерживает .xlsx / .xlsm (через openpyxl, read-only стриминг) и .xls
(через xlrd 2.x). Нормализация и дедупликация — на стороне DAL, здесь
только «взять нужную колонку, вернуть непустые строки в порядке листа».
"""
from __future__ import annotations

from pathlib import Path

import openpyxl


def _cell_to_str(value) -> str:
    """Преобразует значение ячейки в строку.

    - None -> ""  (вызывающий код пропустит пустые)
    - float без дробной части -> "12345"  (Excel хранит числа как float)
    - прочий float -> str(value)
    - int -> str(value)
    - str -> strip()
    """
    if value is None:
        return ""
    if isinstance(value, float):
        if value == int(value):
            return str(int(value))
        return str(value)
    if isinstance(value, int):
        return str(value)
    return str(value).strip()


def read_exclusion_articles(path, *, column: int = 0, skip_header: bool = False) -> list[str]:
    """Читает артикулы из одной колонки Excel-файла (первый лист).

    Parameters
    ----------
    path:
        Путь к файлу (.xlsx, .xlsm или .xls).
    column:
        0-based индекс колонки.
    skip_header:
        Если True — первая строка данных пропускается (заголовок).

    Returns
    -------
    list[str]
        Непустые строки артикулов в порядке листа, без нормализации.
    """
    path = Path(path)
    ext = path.suffix.lower()

    if ext in (".xlsx", ".xlsm"):
        return _read_xlsx(path, column=column, skip_header=skip_header)
    elif ext == ".xls":
        return _read_xls(path, column=column, skip_header=skip_header)
    else:
        raise ValueError(
            f"Неподдерживаемый формат файла: «{ext}». "
            "Допустимые форматы: .xlsx, .xlsm, .xls"
        )


def _read_xlsx(path: Path, *, column: int, skip_header: bool) -> list[str]:
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb.worksheets[0]
        result: list[str] = []
        col1 = column + 1  # openpyxl min_col/max_col are 1-based
        rows = ws.iter_rows(min_col=col1, max_col=col1, values_only=True)
        if skip_header:
            try:
                next(rows)
            except StopIteration:
                return result
        for (val,) in rows:
            s = _cell_to_str(val)
            if s:
                result.append(s)
        return result
    finally:
        wb.close()


def _read_xls(path: Path, *, column: int, skip_header: bool) -> list[str]:
    import xlrd  # deferred — xlrd не нужен для .xlsx
    book = xlrd.open_workbook(str(path))
    sheet = book.sheet_by_index(0)
    result: list[str] = []
    start = 1 if skip_header else 0
    for r in range(start, sheet.nrows):
        val = sheet.cell_value(r, column)
        s = _cell_to_str(val)
        if s:
            result.append(s)
    return result
