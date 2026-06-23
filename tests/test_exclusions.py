"""Tests for engine.exclusions.read_exclusion_articles."""
import sys
import types

import pytest
from openpyxl import Workbook

from engine.exclusions import read_exclusion_articles


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_xlsx(tmp_path, rows, filename="test.xlsx"):
    """Write rows (list of lists) to the first sheet of a new .xlsx file."""
    path = tmp_path / filename
    wb = Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    wb.save(path)
    return path


# ---------------------------------------------------------------------------
# 1. xlsx happy path — with skip_header
# ---------------------------------------------------------------------------

def test_xlsx_skip_header_drops_blank_and_converts_numeric(tmp_path):
    path = _make_xlsx(tmp_path, [
        ["Артикул", "Описание"],   # header row
        ["ABC-001", "деталь"],     # normal string article
        [12345, "число"],          # stored as int → Excel writes as number
        [None, "пустая"],          # blank in target column — must be dropped
        ["XYZ-999", "ещё одна"],   # another string article
    ])
    result = read_exclusion_articles(path, column=0, skip_header=True)
    assert result == ["ABC-001", "12345", "XYZ-999"]


# ---------------------------------------------------------------------------
# 2. xlsx without skip_header — first row IS included
# ---------------------------------------------------------------------------

def test_xlsx_no_skip_header_includes_first_row(tmp_path):
    path = _make_xlsx(tmp_path, [
        ["Артикул"],
        ["ABC-001"],
    ])
    result = read_exclusion_articles(path, column=0, skip_header=False)
    assert result == ["Артикул", "ABC-001"]


# ---------------------------------------------------------------------------
# 3. non-target column
# ---------------------------------------------------------------------------

def test_xlsx_reads_correct_column(tmp_path):
    path = _make_xlsx(tmp_path, [
        ["цена", "ART-1"],
        ["цена2", "ART-2"],
    ])
    # column=1 should read second column
    assert read_exclusion_articles(path, column=1) == ["ART-1", "ART-2"]
    # column=0 should read first column
    assert read_exclusion_articles(path, column=0) == ["цена", "цена2"]


# ---------------------------------------------------------------------------
# 4. unsupported extension raises ValueError
# ---------------------------------------------------------------------------

def test_unsupported_extension_raises(tmp_path):
    path = tmp_path / "data.csv"
    path.write_text("a,b\n")
    with pytest.raises(ValueError, match=r"\.csv"):
        read_exclusion_articles(path)


# ---------------------------------------------------------------------------
# 5. .xls branch via monkeypatched xlrd
# ---------------------------------------------------------------------------

def _make_fake_xlrd(rows, column):
    """Build a minimal fake xlrd module for testing the .xls branch.

    rows is a list of lists; the target column is read per-row.
    """
    fake = types.ModuleType("xlrd")

    class FakeSheet:
        nrows = len(rows)
        _rows = rows

        def cell_value(self, r, c):
            return self._rows[r][c]

    class FakeBook:
        _sheet = FakeSheet()

        def sheet_by_index(self, idx):
            return self._sheet

    def open_workbook(path):  # noqa: ARG001
        return FakeBook()

    fake.open_workbook = open_workbook
    return fake


def test_xls_branch_numeric_conversion_and_blank_dropping(tmp_path, monkeypatch):
    # Rows: header, numeric article, blank, string article
    rows = [
        ["Артикул"],    # header
        [67890.0],      # float that is integer-valued → "67890"
        [None],         # blank — must be dropped
        ["DEF-042"],    # plain string
    ]
    fake_xlrd = _make_fake_xlrd(rows, column=0)
    monkeypatch.setitem(sys.modules, "xlrd", fake_xlrd)

    # The file path must end in .xls; it need not exist because the fake ignores it.
    path = tmp_path / "fake.xls"

    result = read_exclusion_articles(path, column=0, skip_header=True)
    assert result == ["67890", "DEF-042"]


def test_xls_branch_without_skip_header(tmp_path, monkeypatch):
    rows = [
        ["HEAD"],
        ["ART-1"],
    ]
    fake_xlrd = _make_fake_xlrd(rows, column=0)
    monkeypatch.setitem(sys.modules, "xlrd", fake_xlrd)

    path = tmp_path / "fake.xls"
    result = read_exclusion_articles(path, column=0, skip_header=False)
    assert result == ["HEAD", "ART-1"]
