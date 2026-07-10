"""Row dataclasses for the data store.

These mirror the SQLite tables (schema.sql). Secret fields hold the *encrypted*
blob (or None); decrypted values are obtained only via the DAL's dedicated
accessors so secrets are never accidentally logged or rendered.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Connection1C:
    id: int | None = None
    name: str = ""
    kind: str = "server"          # 'server' | 'file' (COM base type; unused for OData)
    srvr: str = ""
    ref: str = ""
    file_path: str = ""
    progid: str = "V83.COMConnector"
    usr: str = ""                 # COM: 1C user; OData: HTTP Basic user
    has_password: bool = False    # whether password_enc is set (secret itself not exposed)
    is_default: bool = False
    source: str = "com"           # 'com' (external COM connection) | 'odata' (HTTP OData)
    # OData source parameters (used only when source == 'odata'). The queries are the
    # 1C OData entity-set requests (paths + $select/$filter); credentials reuse usr/password.
    odata_base_url: str = ""
    odata_nomenclature_query: str = ""
    odata_prices_query: str = ""
    odata_stock_query: str = ""
    odata_producers_query: str = ""
    odata_verify_ssl: bool = True   # False => skip TLS verify (self-signed cert over VPN)


@dataclass
class Cabinet:
    id: int | None = None
    name: str = ""
    api_url: str = "https://b52-api.zzap.pro/api/client/v1/price1c/upload"
    has_api_key: bool = False     # whether api_key_enc is set


@dataclass
class ExclusionList:
    id: int | None = None
    name: str = ""
    articles: str = ""


@dataclass
class Cell:
    id: int | None = None
    name: str = ""
    enabled: bool = False
    connection_id: int | None = None
    cabinet_id: int | None = None
    code_templ: int = 0
    price_type: str = ""
    warehouses: list[str] = field(default_factory=list)
    exclusion_list_id: int | None = None
    include_header: bool = False
    columns: dict[str, int] = field(
        default_factory=lambda: {"producer": 1, "number": 2, "name": 3,
                                 "quantity": 4, "price": 5})


@dataclass
class RunHistory:
    id: int | None = None
    cell_id: int | None = None
    started_at: str = ""
    finished_at: str | None = None
    status: str = ""
    rows_sent: int | None = None
    rows_note: str | None = None
    message: str | None = None
