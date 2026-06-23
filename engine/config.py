"""Engine configuration value objects.

These are plain dataclasses the engine consumes. In the desktop app they are built
by the data layer (SQLite + DPAPI) per cell/cabinet/connection — there is no
config.ini in the app (the old `load_config` ini parser is intentionally dropped;
see SEED_NOTES.md). Keeping these UI-agnostic lets the engine be unit-tested in
isolation.
"""
from __future__ import annotations

from dataclasses import dataclass

# The ONE correct ZZap endpoint (verified by a real production upload; see
# PROJECT_MEMORY.md §4). Do not change. NOT `.../v1/price_upload` (404).
DEFAULT_ZZAP_API_URL = "https://b52-api.zzap.pro/api/client/v1/price1c/upload"

# 1C COM connector ProgID (same across 1C versions).
DEFAULT_COM_PROGID = "V83.COMConnector"


@dataclass
class ZzapConfig:
    """One ZZap cabinet/template target for an upload."""
    api_key: str
    code_templ: int
    api_url: str = DEFAULT_ZZAP_API_URL
    # Staging / dry-run: build the XLSX but do NOT POST to ZZap. Newly added cells
    # default to staging for safety (see PROMPT.md "Safety first").
    test_mode: bool = True


@dataclass
class ComConfig:
    """A 1C external-connection (COM) target plus the query to run."""
    progid: str
    conn_string: str
    query: str


@dataclass
class OutputConfig:
    """How the XLSX is laid out (column order under the ZZap template mapping)."""
    file_name: str = "price.xlsx"
    # ZZap templates are configured with NO header row (data starts at row 1),
    # so the app writes files with include_header=False. See PROJECT_MEMORY.md §5.
    include_header: bool = False
    col_producer: int = 1
    col_number: int = 2
    col_name: int = 3
    col_quantity: int = 4
    col_price: int = 5

    @property
    def columns(self) -> dict[str, int]:
        return {
            "producer": self.col_producer,
            "number": self.col_number,
            "name": self.col_name,
            "quantity": self.col_quantity,
            "price": self.col_price,
        }


@dataclass
class OdataConfig:
    """OData source config (future 2nd base / alt path; not used in MVP)."""
    base_url: str
    username: str
    password: str
    nomenclature_query: str
    prices_query: str
    stock_query: str
    producers_query: str
    nom_key_field: str = "Ref_Key"
    nom_number_field: str = "Артикул"
    nom_name_field: str = "Description"
    nom_producer_field: str = "Производитель_Key"
    price_key_field: str = "Номенклатура_Key"
    price_value_field: str = "Цена"
    stock_key_field: str = "Номенклатура_Key"
    stock_qty_field: str = "КоличествоBalance"
    producer_key_field: str = "Ref_Key"
    producer_name_field: str = "Description"
