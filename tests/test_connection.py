"""ConnectionManager unit tests with a fake COM connection (no live 1C)."""
from app.db.models import Connection1C
from app.services.connection import (ConnectionManager, describe_1c_error,
                                     error_text)


class FakeCom1C:
    """Mimics engine.sources.com.Com1C (context manager + query) with canned data."""
    last_instance: "FakeCom1C | None" = None

    def __init__(self, conn_string, progid="V83.COMConnector"):
        self.conn_string = conn_string
        self.progid = progid
        self.queries: list[tuple[str, int]] = []
        FakeCom1C.last_instance = self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def query(self, text, columns):
        self.queries.append((text, columns))
        if "Справочник.Склады" in text:
            return [("Квант (Новые) 3 этаж",), ("Товары по партиям",), ("",)]  # blank dropped
        if "Справочник.ВидыЦен" in text:
            return [("ZZap",), ("Розничная",), ("ZZap",)]                       # dup deduped
        return [(1,)]


class FailingCom1C:
    """Factory whose connection raises on __enter__ (simulates a failed Connect)."""
    def __init__(self, exc):
        self._exc = exc

    def __call__(self, conn_string, progid="V83.COMConnector"):
        return self

    def __enter__(self):
        raise self._exc

    def __exit__(self, *exc):
        return False


def _server_conn():
    return Connection1C(name="base1", kind="server", srvr="Serv1C", ref="ut2025",
                        usr="Натали", progid="V83.COMConnector")


def test_build_conn_string_server():
    cs = ConnectionManager.build_conn_string(_server_conn(), "secret")
    assert cs == 'Srvr="Serv1C";Ref="ut2025";Usr="Натали";Pwd="secret";'


def test_build_conn_string_file():
    conn = Connection1C(name="f", kind="file", file_path=r"C:\base", usr="U")
    cs = ConnectionManager.build_conn_string(conn, "p")
    assert cs == r'File="C:\base";Usr="U";Pwd="p";'


def test_build_conn_string_escapes_quotes_in_password():
    cs = ConnectionManager.build_conn_string(_server_conn(), 'pa"ss')
    assert 'Pwd="pa""ss";' in cs


def test_build_conn_string_server_requires_srvr_and_ref():
    import pytest
    with pytest.raises(ValueError):
        ConnectionManager.build_conn_string(Connection1C(kind="server"), "p")


def test_test_connection_success_uses_password():
    mgr = ConnectionManager(connection_factory=FakeCom1C)
    res = mgr.test_connection(_server_conn(), "p")
    assert res.ok is True
    assert "установлено" in res.message
    assert 'Pwd="p";' in FakeCom1C.last_instance.conn_string


def test_test_connection_maps_external_connection_right():
    mgr = ConnectionManager(
        connection_factory=FailingCom1C(RuntimeError("Внешнее соединение не разрешено")))
    res = mgr.test_connection(_server_conn(), "p")
    assert res.ok is False
    assert "Внешнее соединение" in res.message
    assert res.detail   # raw detail captured for logs


def test_test_connection_never_raises_on_bad_config():
    mgr = ConnectionManager(connection_factory=FakeCom1C)
    res = mgr.test_connection(Connection1C(kind="server"), "p")  # missing srvr/ref
    assert res.ok is False


def test_discover_cleans_and_dedupes_over_one_connection():
    mgr = ConnectionManager(connection_factory=FakeCom1C)
    disc = mgr.discover(_server_conn(), "p")
    assert disc.warehouses == ["Квант (Новые) 3 этаж", "Товары по партиям"]
    assert disc.price_types == ["ZZap", "Розничная"]
    assert len(FakeCom1C.last_instance.queries) == 2   # both via a single connection


def test_to_com_config_bridges_to_engine():
    mgr = ConnectionManager(connection_factory=FakeCom1C)
    cfg = mgr.to_com_config(_server_conn(), "p", "ВЫБРАТЬ 1")
    assert cfg.progid == "V83.COMConnector"
    assert 'Srvr="Serv1C"' in cfg.conn_string
    assert cfg.query == "ВЫБРАТЬ 1"


def test_describe_1c_error_typelib():
    msg = describe_1c_error(RuntimeError("Библиотека не зарегистрирована"))
    assert "не зарегистрирован" in msg.lower()


def test_describe_1c_error_password():
    msg = describe_1c_error(RuntimeError("Неверный пароль пользователя"))
    assert "пароль" in msg.lower()


def test_describe_1c_error_server():
    msg = describe_1c_error(RuntimeError("Сервер не обнаружен"))
    assert "сервер" in msg.lower()


def test_error_text_flattens_com_error_like_tuple():
    # com_error-shaped: (hresult, msg, excinfo, argerr)
    exc = Exception(-2147467259, "Ошибка", ("wcode", "1C:Предприятие",
                    "Внешнее соединение не разрешено", "", 0, 0), None)
    blob = error_text(exc)
    assert "Внешнее соединение не разрешено" in blob
    assert "1C:Предприятие" in blob


def test_error_text_redacts_password_when_error_echoes_conn_string():
    cs = 'Srvr="Serv1C";Ref="ut2025";Usr="Натали";Pwd="secret123";'
    blob = error_text(Exception(f"Connect failed: {cs}"))
    assert "secret123" not in blob       # password never leaks
    assert 'Pwd="***"' in blob           # token redacted
    assert 'Usr="***"' in blob
    assert "Serv1C" in blob              # non-secret parts preserved
    assert "ut2025" in blob
