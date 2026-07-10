"""The PriceSource interface contract (lets the engine be tested without live 1C)."""
import pytest

from engine.config import ComConfig, OdataConfig
from engine.models import PriceRow
from engine.sources import PriceSource
from engine.sources.com import ComPriceSource
from engine.sources.odata import _EMPTY_GUID, OdataPriceSource


class FakeSource:
    def __init__(self, rows):
        self._rows = rows

    def fetch_rows(self):
        return self._rows


def test_fake_source_satisfies_protocol():
    s = FakeSource([PriceRow("a", "1", "n", 1, 2.0)])
    assert isinstance(s, PriceSource)
    assert s.fetch_rows()[0].number == "1"


def test_com_source_satisfies_protocol():
    s = ComPriceSource(ComConfig(progid="V83.COMConnector", conn_string="x", query="y"))
    assert isinstance(s, PriceSource)


def test_com_source_rejects_empty_conn_string():
    s = ComPriceSource(ComConfig(progid="V83.COMConnector", conn_string="", query="q"))
    with pytest.raises(ValueError, match="conn_string"):
        s.fetch_rows()


def test_com_source_rejects_empty_query():
    s = ComPriceSource(ComConfig(progid="V83.COMConnector", conn_string="x", query=""))
    with pytest.raises(ValueError, match="запрос"):
        s.fetch_rows()


# --- OData source (fake HTTP session, no live 1C) ---------------------------
class _FakeResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(str(self.status_code), response=self)

    def json(self):
        return self._payload


class _FakeSession:
    """Minimal stand-in for requests.Session: returns queued responses per GET."""
    def __init__(self, responses):
        self._responses = list(responses)
        self.get_calls: list[str] = []
        self.headers: dict = {}
        self.auth = None

    def get(self, url, timeout=None):
        self.get_calls.append(url)
        return self._responses.pop(0)


def _odata_cfg(**over):
    base = dict(base_url="http://host/base/odata/standard.odata",
                username="", password="",
                nomenclature_query="Catalog_Номенклатура",
                prices_query="", stock_query="", producers_query="")
    base.update(over)
    return OdataConfig(**base)


def test_odata_source_satisfies_protocol():
    assert isinstance(OdataPriceSource(_odata_cfg(), session=_FakeSession([])), PriceSource)


def test_odata_source_applies_verify_ssl_to_session():
    session = _FakeSession([])
    session.verify = True
    OdataPriceSource(_odata_cfg(verify_ssl=False), session=session)
    assert session.verify is False
    session2 = _FakeSession([])
    OdataPriceSource(_odata_cfg(verify_ssl=True), session=session2)
    assert session2.verify is True


def test_odata_probe_hits_service_root_json():
    session = _FakeSession([_FakeResp({"value": []})])
    OdataPriceSource(_odata_cfg(), session=session).probe()
    assert session.get_calls == ["http://host/base/odata/standard.odata/?$format=json"]


def test_odata_fetch_rows_joins_nomenclature_prices_stock_producers():
    nom = {"value": [{"Ref_Key": "g1", "Артикул": "A-1", "Description": "Деталь 1",
                      "Производитель_Key": "p1"},
                     {"Ref_Key": "g2", "Артикул": "B-2", "Description": "Деталь 2",
                      "Производитель_Key": _EMPTY_GUID}]}
    prices = {"value": [{"Номенклатура_Key": "g1", "Цена": 100.0},
                        {"Номенклатура_Key": "g2", "Цена": 50.5}]}
    stock = {"value": [{"Номенклатура_Key": "g1", "КоличествоBalance": 5},
                       {"Номенклатура_Key": "g2", "КоличествоBalance": 2}]}
    producers = {"value": [{"Ref_Key": "p1", "Description": "BrandA"}]}
    # fetch order: nomenclature, prices, stock, producers
    session = _FakeSession([_FakeResp(nom), _FakeResp(prices), _FakeResp(stock),
                            _FakeResp(producers)])
    cfg = _odata_cfg(prices_query="P", stock_query="S", producers_query="PR")

    rows = OdataPriceSource(cfg, session=session).fetch_rows()

    assert len(rows) == 2
    r1 = rows[0]
    assert (r1.producer, r1.number, r1.name, r1.quantity, r1.price) == \
        ("BrandA", "A-1", "Деталь 1", 5.0, 100.0)
    r2 = rows[1]
    # empty-GUID producer resolves to "" (no producer)
    assert r2.producer == "" and r2.number == "B-2" and r2.price == 50.5


def test_odata_stock_is_summed_across_warehouse_rows():
    # The balance query may return one row per warehouse; the price list needs the
    # TOTAL per item (COM did СУММА(...) over the chosen warehouses).
    nom = {"value": [{"Ref_Key": "g1", "Артикул": "A-1", "Description": "Деталь",
                      "Производитель_Key": _EMPTY_GUID}]}
    stock = {"value": [{"Номенклатура_Key": "g1", "КоличествоBalance": 3},
                       {"Номенклатура_Key": "g1", "КоличествоBalance": 4},
                       {"Номенклатура_Key": "g1", "КоличествоBalance": 0.5}]}
    session = _FakeSession([_FakeResp(nom), _FakeResp(stock)])
    cfg = _odata_cfg(stock_query="S")

    rows = OdataPriceSource(cfg, session=session).fetch_rows()

    assert rows[0].quantity == 7.5          # 3 + 4 + 0.5, not "last row wins"


def test_odata_conflicting_prices_keep_first_and_warn(caplog):
    # Several price types in the slice -> ambiguous. Keep the first deterministically
    # and warn loudly so a misconfigured query is caught before a wrong upload.
    nom = {"value": [{"Ref_Key": "g1", "Артикул": "A-1", "Description": "Деталь",
                      "Производитель_Key": _EMPTY_GUID}]}
    prices = {"value": [{"Номенклатура_Key": "g1", "Цена": 100.0},
                        {"Номенклатура_Key": "g1", "Цена": 250.0}]}
    session = _FakeSession([_FakeResp(nom), _FakeResp(prices)])
    cfg = _odata_cfg(prices_query="P")

    with caplog.at_level("WARNING"):
        rows = OdataPriceSource(cfg, session=session).fetch_rows()

    assert rows[0].price == 100.0                       # first wins, deterministic
    assert "несколько РАЗНЫХ цен" in caplog.text
    assert "$filter" in caplog.text


def test_odata_repeated_identical_price_does_not_warn(caplog):
    nom = {"value": [{"Ref_Key": "g1", "Артикул": "A-1", "Description": "Д",
                      "Производитель_Key": _EMPTY_GUID}]}
    prices = {"value": [{"Номенклатура_Key": "g1", "Цена": 100.0},
                        {"Номенклатура_Key": "g1", "Цена": 100.0}]}
    session = _FakeSession([_FakeResp(nom), _FakeResp(prices)])

    with caplog.at_level("WARNING"):
        rows = OdataPriceSource(_odata_cfg(prices_query="P"), session=session).fetch_rows()

    assert rows[0].price == 100.0
    assert "несколько РАЗНЫХ цен" not in caplog.text


def test_odata_fetch_rows_follows_next_link_paging():
    page1 = {"value": [{"Ref_Key": "g1", "Артикул": "A-1", "Description": "n1",
                        "Производитель_Key": _EMPTY_GUID}],
             "@odata.nextLink": "http://host/base/odata/standard.odata/Page2?$format=json"}
    page2 = {"value": [{"Ref_Key": "g2", "Артикул": "A-2", "Description": "n2",
                        "Производитель_Key": _EMPTY_GUID}]}
    session = _FakeSession([_FakeResp(page1), _FakeResp(page2)])

    rows = OdataPriceSource(_odata_cfg(), session=session).fetch_rows()

    assert [r.number for r in rows] == ["A-1", "A-2"]
    assert len(session.get_calls) == 2          # followed the nextLink
