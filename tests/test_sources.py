"""The PriceSource interface contract (lets the engine be tested without live 1C)."""
import pytest

from engine.config import ComConfig
from engine.models import PriceRow
from engine.sources import PriceSource
from engine.sources.com import ComPriceSource


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
