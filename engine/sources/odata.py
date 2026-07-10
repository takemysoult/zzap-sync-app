"""Чтение прайс-листа из 1С через стандартный интерфейс OData (HTTP).

Альтернатива COM: не нужен COM-коннектор, совпадение разрядности и дочерний процесс —
работает по сети на любой ОС. Выбирается для подключения в приложении
(Connection1C.source == 'odata'). Реализует тот же интерфейс PriceSource, что и
ComPriceSource; фильтр по складу/виду цены задаётся в самих запросах OData ($filter).
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
        # Self-signed 1C publication over a VPN (WireGuard) by IP: skip TLS verification
        # when configured, and silence urllib3's per-request InsecureRequestWarning so
        # the log isn't spammed. The decision is explicit and stored per connection.
        self.session.verify = cfg.verify_ssl
        if not cfg.verify_ssl:
            try:
                from urllib3.exceptions import InsecureRequestWarning
                requests.packages.urllib3.disable_warnings(InsecureRequestWarning)
            except Exception:  # pragma: no cover - warning suppression is best-effort
                pass

    def _url(self, query: str) -> str:
        sep = "&" if "?" in query else "?"
        return f"{self.cfg.base_url}/{query}{sep}$format=json"

    def probe(self) -> None:
        """Lightweight connectivity/auth check for «Проверить соединение».

        GETs the OData service document (the base URL) — this validates the address,
        credentials and reachability without needing a configured query. Raises the
        underlying requests exception on failure (the caller maps it to an RU message).
        """
        resp = self.session.get(self._url(""), timeout=30)
        resp.raise_for_status()

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
            # Одна цена на позицию. Если срез вернул НЕСКОЛЬКО видов цен, выбрать
            # «правильную» здесь невозможно — берём первую (детерминированно) и громко
            # предупреждаем: запрос цен нужно сузить до одного вида цены через $filter.
            conflicting = 0
            for r in self._get_all(cfg.prices_query):
                key = r.get(cfg.price_key_field)
                if key is None:
                    continue
                value = _to_number(r.get(cfg.price_value_field))
                if key in prices:
                    if prices[key] != value:
                        conflicting += 1
                    continue
                prices[key] = value
            if conflicting:
                log.warning(
                    "OData: у %d позиций найдено несколько РАЗНЫХ цен — вероятно, срез "
                    "вернул больше одного вида цены. Ограничьте запрос цен одним видом "
                    "цены через $filter, иначе в выгрузку попадёт произвольная цена.",
                    conflicting)

        stock: dict[str, float] = {}
        if cfg.stock_query:
            log.info("OData: читаю остатки...")
            # Остатки СУММИРУЮТСЯ по позиции: запрос может вернуть строки в разрезе
            # складов, а прайсу нужен общий остаток (COM-запрос делал СУММА(...) по
            # выбранным складам). Если запрос уже агрегирует — строка одна, сумма = ей.
            for r in self._get_all(cfg.stock_query):
                key = r.get(cfg.stock_key_field)
                if key is not None:
                    stock[key] = stock.get(key, 0.0) + _to_number(
                        r.get(cfg.stock_qty_field))

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
