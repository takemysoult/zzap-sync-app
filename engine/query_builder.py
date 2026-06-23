"""Dynamic 1C query construction (pure, no COM).

Two jobs:
  1. `build_price_query` — the proven 5-column price query (PROJECT_MEMORY §3), built
     per cell from the chosen warehouse(s) + price type. Values are inlined as 1C string
     literals (the production CLI did the same; the engine's COM layer does not bind
     parameters). Inlining is done safely: every value is wrapped in double quotes with
     embedded quotes doubled, so a warehouse/price-type name cannot break or inject into
     the query.
  2. `build_catalog_names_query` — list the names in a catalog (warehouses, price types)
     for the live dropdowns, excluding deletion-marked rows (and optionally folders).

The output column order is ALWAYS: Производитель, Номер, Наименование, Количество, Цена.
"""
from __future__ import annotations

# Catalogs discovered for the cell dropdowns (PROJECT_MEMORY §3 / explore_1c.py).
WAREHOUSE_CATALOG = "Справочник.Склады"
PRICE_TYPE_CATALOG = "Справочник.ВидыЦен"


def quote_1c(value: str) -> str:
    """Render a Python string as a safe 1C string literal.

    1C string literals are double-quoted; an embedded `"` is escaped by doubling it.
    This makes inlining warehouse / price-type names injection-safe.
    """
    return '"' + (value or "").replace('"', '""') + '"'


def build_price_query(warehouses: list[str], price_type: str) -> str:
    """Build the proven price query for the given warehouse(s) + price type.

    Mirrors PROJECT_MEMORY §3 exactly, substituting the warehouse list into
    `Склад.Наименование В (...)` and the price type into the СрезПоследних filter.
    Raises ValueError if no warehouse or no price type is given.
    """
    if not warehouses:
        raise ValueError("Не выбран ни один склад для ячейки.")
    if not any((w or "").strip() for w in warehouses):
        raise ValueError("Список складов пуст (только пустые значения).")
    if not (price_type or "").strip():
        raise ValueError("Не выбран вид цены для ячейки.")

    sklady = ", ".join(quote_1c(w) for w in warehouses if (w or "").strip())
    vid = quote_1c(price_type)

    return (
        "ВЫБРАТЬ\n"
        '\tЕСТЬNULL(Ном.Производитель.Наименование, "") КАК Производитель,\n'
        "\tНом.Артикул КАК Номер,\n"
        "\tНом.Наименование КАК Наименование,\n"
        "\tЕСТЬNULL(Остатки.Количество, 0) КАК Количество,\n"
        "\tЕСТЬNULL(Цены.Цена, 0) КАК Цена\n"
        "ИЗ Справочник.Номенклатура КАК Ном\n"
        "\tЛЕВОЕ СОЕДИНЕНИЕ (\n"
        "\t\tВЫБРАТЬ Ост.Номенклатура КАК Номенклатура, СУММА(Ост.ВНаличииОстаток) КАК Количество\n"
        f"\t\tИЗ РегистрНакопления.ТоварыНаСкладах.Остатки(, Склад.Наименование В ({sklady})) КАК Ост\n"
        "\t\tСГРУППИРОВАТЬ ПО Ост.Номенклатура) КАК Остатки\n"
        "\t\tПО Остатки.Номенклатура = Ном.Ссылка\n"
        "\tЛЕВОЕ СОЕДИНЕНИЕ РегистрСведений.ЦеныНоменклатуры.СрезПоследних(, "
        f"ВидЦены.Наименование = {vid}) КАК Цены\n"
        "\t\tПО Цены.Номенклатура = Ном.Ссылка\n"
        "ГДЕ НЕ Ном.ПометкаУдаления И НЕ Ном.ЭтоГруппа\n"
        "\tИ ЕСТЬNULL(Остатки.Количество, 0) > 0 И ЕСТЬNULL(Цены.Цена, 0) > 0"
    )


def build_catalog_names_query(catalog: str, exclude_groups: bool = True) -> str:
    """Query the `Наименование` of every (non-deleted) row in a catalog, ordered.

    exclude_groups adds `И НЕ ЭтоГруппа`; set False for non-hierarchical catalogs
    (referencing ЭтоГруппа on a flat catalog raises a 1C error). Warehouses are
    hierarchical (exclude_groups=True); price types are flat (exclude_groups=False).
    Confirm this against the live base during Phase 1 validation.
    """
    where = ["НЕ Спр.ПометкаУдаления"]
    if exclude_groups:
        where.append("НЕ Спр.ЭтоГруппа")
    return (
        "ВЫБРАТЬ Спр.Наименование КАК Наименование\n"
        f"ИЗ {catalog} КАК Спр\n"
        f"ГДЕ {' И '.join(where)}\n"
        "УПОРЯДОЧИТЬ ПО Спр.Наименование"
    )
