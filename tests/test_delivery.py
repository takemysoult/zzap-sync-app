from engine.delivery import Delivery


def test_journal_appends_lines(tmp_path):
    d = Delivery(tmp_path)
    d.journal("OK", "hello")
    d.journal("FAIL", "world")
    content = d.journal_path.read_text(encoding="utf-8")
    assert "OK\thello" in content
    assert "FAIL\tworld" in content


def test_mark_pending_copies_file_and_records_state(tmp_path):
    d = Delivery(tmp_path)
    built = tmp_path / "built.xlsx"
    built.write_bytes(b"XLSXDATA")

    d.mark_pending(built, "price.xlsx", "no network")

    pending = d.get_pending()
    assert pending is not None
    assert pending["file_name"] == "price.xlsx"
    assert d.pending_file.read_bytes() == b"XLSXDATA"


def test_record_success_clears_pending_and_writes_state(tmp_path):
    d = Delivery(tmp_path)
    built = tmp_path / "built.xlsx"
    built.write_bytes(b"data")
    d.mark_pending(built, "price.xlsx", "boom")
    assert d.get_pending() is not None

    d.record_success("price.xlsx", 100, None)

    state = d.read_state()
    assert state["last_rows"] == 100
    assert "last_success" in state
    assert d.get_pending() is None
    assert not d.pending_file.exists()


def test_per_cell_isolation(tmp_path):
    a = Delivery(tmp_path / "cell_a")
    b = Delivery(tmp_path / "cell_b")
    a.record_success("p.xlsx", 10, None)
    assert a.read_state().get("last_rows") == 10
    assert b.read_state() == {}          # b is independent
