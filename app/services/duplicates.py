"""Duplicate-across-warehouses detector (PROJECT_MEMORY §4 rule).

ZZap deduplicates rows by article+brand *within a template*, but NOT across
templates of the same cabinet. So if one cabinet's goods are split across two
templates by warehouse, an article that is stocked on warehouses belonging to
both cells shows up twice in that cabinet. This is a *configuration* hazard we can
warn about before any upload — no 1C round-trip needed.

`find_duplicate_risks` inspects the saved cells and flags every pair of **enabled
cells of the same cabinet that target different templates** (`code_templ`):

  - they share one or more warehouses  -> high confidence (the same warehouse feeds
    two templates; any article there is guaranteed to double);
  - their warehouses are disjoint       -> possible (an article stocked on a
    warehouse from each side still doubles — data-dependent, the §4 example).

The GUI (Phase 3) renders these as a warning banner; this function is the rule.
Same-cabinet/same-template pairs are a different problem (the runs overwrite each
other, not duplicate) and are intentionally out of scope here.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

from ..db.models import Cell

# Confidence levels for a flagged pair.
SHARED_WAREHOUSE = "shared_warehouse"   # a warehouse feeds both cells -> guaranteed dup
SPLIT_WAREHOUSE = "split_warehouse"     # disjoint warehouses -> possible dup (§4 example)


@dataclass
class DuplicateRisk:
    cabinet_id: int
    cell_ids: tuple[int, int]
    cell_names: tuple[str, str]
    shared_warehouses: list[str]
    confidence: str          # SHARED_WAREHOUSE | SPLIT_WAREHOUSE
    message: str             # human-readable RU, safe for the UI


def _norm_wh(name: str) -> str:
    return (name or "").strip().casefold()


def find_duplicate_risks(cells: list[Cell]) -> list[DuplicateRisk]:
    """Flag same-cabinet, different-template enabled cell pairs that can double an
    article in a ZZap cabinet. Deterministic order (by ascending cell id)."""
    # Only enabled, saved cells that actually target a cabinet can collide.
    relevant = [c for c in cells
                if c.enabled and c.cabinet_id is not None and c.id is not None]
    by_cabinet: dict[int, list[Cell]] = {}
    for c in relevant:
        by_cabinet.setdefault(c.cabinet_id, []).append(c)

    risks: list[DuplicateRisk] = []
    for cabinet_id in sorted(by_cabinet):          # ascending cabinet id, then cell id
        group = by_cabinet[cabinet_id]
        group.sort(key=lambda c: c.id)
        for a, b in combinations(group, 2):
            # Same template => one run replaces the other, not a duplicate.
            if a.code_templ == b.code_templ:
                continue
            shared = _shared_warehouses(a, b)
            confidence = SHARED_WAREHOUSE if shared else SPLIT_WAREHOUSE
            risks.append(DuplicateRisk(
                cabinet_id=cabinet_id,
                cell_ids=(a.id, b.id),
                cell_names=(a.name, b.name),
                shared_warehouses=shared,
                confidence=confidence,
                message=_message(a, b, shared),
            ))
    return risks


def _shared_warehouses(a: Cell, b: Cell) -> list[str]:
    """Warehouses present in BOTH cells (case-insensitive), in cell `a`'s order."""
    b_norm = {_norm_wh(w) for w in b.warehouses}
    out, seen = [], set()
    for w in a.warehouses:
        key = _norm_wh(w)
        if key in b_norm and key not in seen and key:
            seen.add(key)
            out.append(w.strip())
    return out


def _message(a: Cell, b: Cell, shared: list[str]) -> str:
    head = (f"Ячейки «{a.name}» и «{b.name}» относятся к одному кабинету ZZap, "
            f"но грузят в разные шаблоны (code_templ {a.code_templ} и {b.code_templ}).")
    if shared:
        return (f"{head} Общие склады: {', '.join(shared)}. Артикул с такого склада "
                f"задвоится в кабинете — разнесите склады или объедините ячейки.")
    return (f"{head} Склады разделены между шаблонами: товар, который есть на складах "
            f"обеих ячеек, появится в кабинете дважды. Проверьте пересечение остатков.")
