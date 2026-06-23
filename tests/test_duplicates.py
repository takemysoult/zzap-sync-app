"""Duplicate-across-warehouses detector (PROJECT_MEMORY §4)."""
from app.db.models import Cell
from app.services.duplicates import (SHARED_WAREHOUSE, SPLIT_WAREHOUSE,
                                     find_duplicate_risks)


def _cell(cid, cabinet_id, code_templ, warehouses, *, enabled=True, name=None):
    return Cell(id=cid, name=name or f"cell{cid}", enabled=enabled,
                cabinet_id=cabinet_id, code_templ=code_templ, price_type="ZZap",
                warehouses=warehouses)


def test_single_cell_has_no_risk():
    assert find_duplicate_risks([_cell(1, 10, 100, ["A"])]) == []


def test_shared_warehouse_is_high_confidence():
    cells = [_cell(1, 10, 100, ["Склад A", "B"]), _cell(2, 10, 200, ["b", "C"])]
    risks = find_duplicate_risks(cells)
    assert len(risks) == 1
    r = risks[0]
    assert r.cabinet_id == 10
    assert r.cell_ids == (1, 2)
    assert r.confidence == SHARED_WAREHOUSE
    assert [w.lower() for w in r.shared_warehouses] == ["b"]
    assert "Общие склады" in r.message


def test_disjoint_warehouses_are_possible_confidence():
    cells = [_cell(1, 10, 100, ["A"]), _cell(2, 10, 200, ["B"])]
    risks = find_duplicate_risks(cells)
    assert len(risks) == 1
    assert risks[0].confidence == SPLIT_WAREHOUSE
    assert risks[0].shared_warehouses == []


def test_same_template_same_cabinet_is_not_a_duplicate():
    # Same code_templ => the runs overwrite each other, not duplicate.
    assert find_duplicate_risks([_cell(1, 10, 100, ["A"]), _cell(2, 10, 100, ["B"])]) == []


def test_different_cabinets_never_collide():
    assert find_duplicate_risks([_cell(1, 10, 100, ["A"]), _cell(2, 20, 200, ["A"])]) == []


def test_disabled_cells_are_ignored():
    cells = [_cell(1, 10, 100, ["A"]), _cell(2, 10, 200, ["A"], enabled=False)]
    assert find_duplicate_risks(cells) == []


def test_three_same_cabinet_cells_yield_all_pairs():
    cells = [_cell(1, 10, 100, ["A"]), _cell(2, 10, 200, ["B"]), _cell(3, 10, 300, ["A"])]
    by_pair = {r.cell_ids: r for r in find_duplicate_risks(cells)}
    assert set(by_pair) == {(1, 2), (1, 3), (2, 3)}
    assert by_pair[(1, 3)].confidence == SHARED_WAREHOUSE   # both have "A"
    assert by_pair[(1, 2)].confidence == SPLIT_WAREHOUSE
