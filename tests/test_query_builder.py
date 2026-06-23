import pytest

from engine.query_builder import (PRICE_TYPE_CATALOG, WAREHOUSE_CATALOG,
                                  build_catalog_names_query, build_price_query,
                                  quote_1c)


def test_quote_1c_escapes_double_quotes():
    assert quote_1c('A"B') == '"A""B"'
    assert quote_1c("Квант") == '"Квант"'
    assert quote_1c("") == '""'


def test_price_query_inlines_warehouses_and_price_type():
    q = build_price_query(["Квант (Новые) 3 этаж", "Товары по партиям"], "ZZap")
    assert 'Склад.Наименование В ("Квант (Новые) 3 этаж", "Товары по партиям")' in q
    assert 'ВидЦены.Наименование = "ZZap"' in q


def test_price_query_has_five_columns_in_order():
    q = build_price_query(["W"], "ZZap")
    order = [q.index(a) for a in ("КАК Производитель", "КАК Номер", "КАК Наименование",
                                  "КАК Количество", "КАК Цена")]
    assert order == sorted(order)   # appear in the required order


def test_price_query_has_proven_filters():
    q = build_price_query(["W"], "ZZap")
    assert "НЕ Ном.ПометкаУдаления И НЕ Ном.ЭтоГруппа" in q
    assert "ЕСТЬNULL(Остатки.Количество, 0) > 0" in q
    assert "ЕСТЬNULL(Цены.Цена, 0) > 0" in q


def test_price_query_is_injection_safe():
    # An embedded quote is doubled, so the malicious text stays inside ONE literal.
    q = build_price_query(['Зло"); ОПАСНО'], "P")
    assert '"Зло""); ОПАСНО"' in q


def test_price_query_skips_blank_warehouses():
    q = build_price_query(["W1", "  ", "W2"], "P")
    assert 'В ("W1", "W2")' in q


def test_price_query_requires_a_warehouse():
    with pytest.raises(ValueError, match="склад"):
        build_price_query([], "ZZap")
    with pytest.raises(ValueError, match="склад"):
        build_price_query(["   "], "ZZap")


def test_price_query_requires_a_price_type():
    with pytest.raises(ValueError, match="цен"):
        build_price_query(["W"], "")


def test_warehouse_catalog_query_excludes_groups():
    q = build_catalog_names_query(WAREHOUSE_CATALOG, exclude_groups=True)
    assert "Справочник.Склады" in q
    assert "НЕ Спр.ПометкаУдаления" in q
    assert "НЕ Спр.ЭтоГруппа" in q
    assert "УПОРЯДОЧИТЬ ПО Спр.Наименование" in q


def test_price_type_catalog_query_is_flat():
    q = build_catalog_names_query(PRICE_TYPE_CATALOG, exclude_groups=False)
    assert "Справочник.ВидыЦен" in q
    assert "НЕ Спр.ПометкаУдаления" in q
    assert "ЭтоГруппа" not in q   # flat catalog: referencing it would error in 1C
