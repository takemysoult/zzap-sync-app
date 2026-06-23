"""Чтение прайс-листа из 1С через стандартный интерфейс OData.

Запасной/будущий источник (вторая база, недоступная по COM с этого ПК).
Реализует тот же интерфейс PriceSource, что и ComPriceSource.
"""
from __future__ import annotations

import logging
from typing import Any

import requests
from requests.auth import HTTPBasicAuth

from ..config import OdataConfig
from ..models import PriceRow

log = logging.getLogger(__name__)

# Пустой GUID 1С — означает «значение не заполнено».
_EMPTY_GUID = "00000000-0000-0000-0000-000000000000"


def _to_number(value: Any) -> float:
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


class OdataPriceSource:
    def __init__(self, cfg: OdataConfig, session: requests.Session | None = None):
        self.cfg = cfg
        self.session = session or requests.Session()
        if cfg.username:
            self.session.auth = HTTPBasicAuth(cfg.username, cfg.password)
        self.session.headers.update({"Accept": "application/json"})

    def _url(self, query: str) -> str:
        sep = "&" if "?" in query else "?"
        return f"{self.cfg.base_url}/{query}{sep}$format=json"

    def _get_all(self, query: str) -> list[dict]:
        """GET с автоматическим проходом по постраничным @odata.nextLink."""
        url = self._url(query)
        rows: list[dict] = []
        while url:
            resp = self.session.get(url, timeout=120)
            resp.raise_for_status()
            data = resp.json()
            rows.extend(data.get("value", []))
            url = data.get("@odata.nextLink") or data.get("odata.nextLink")
        return rows

    def fetch_rows(self) -> list[PriceRow]:
        cfg = self.cfg

        log.info("OData: читаю номенклатуру...")
        nomenclature = self._get_all(cfg.nomenclature_query)
        log.info("OData: получено позиций номенклатуры: %d", len(nomenclature))

        prices: dict[str, float] = {}
        if cfg.prices_query:
            log.info("OData: читаю цены...")
            for r in self._get_all(cfg.prices_query):
                key = r.get(cfg.price_key_field)
                if key is not None:
                    prices[key] = _to_number(r.get(cfg.price_value_field))

        stock: dict[str, float] = {}
        if cfg.stock_query:
            log.info("OData: читаю остатки...")
            for r in self._get_all(cfg.stock_query):
                key = r.get(cfg.stock_key_field)
                if key is not None:
                    stock[key] = _to_number(r.get(cfg.stock_qty_field))

        producers: dict[str, str] = {}
        if cfg.producers_query:
            log.info("OData: читаю производителей...")
            for r in self._get_all(cfg.producers_query):
                key = r.get(cfg.producer_key_field)
                if key is not None:
                    producers[key] = str(r.get(cfg.producer_name_field) or "").strip()

        rows: list[PriceRow] = []
        for n in nomenclature:
            key = n.get(cfg.nom_key_field)
            raw_producer = n.get(cfg.nom_producer_field)
            if producers and raw_producer not in (None, _EMPTY_GUID):
                producer = producers.get(raw_producer, "")
            else:
                producer = "" if raw_producer in (None, _EMPTY_GUID) else str(raw_producer)

            rows.append(
                PriceRow(
                    producer=producer.strip(),
                    number=str(n.get(cfg.nom_number_field) or "").strip(),
                    name=str(n.get(cfg.nom_name_field) or "").strip(),
                    quantity=stock.get(key, 0.0),
                    price=prices.get(key, 0.0),
                )
            )
        return rows


def fetch_rows(cfg: OdataConfig) -> list[PriceRow]:
    """Backward-compatible functional entry point."""
    return OdataPriceSource(cfg).fetch_rows()
