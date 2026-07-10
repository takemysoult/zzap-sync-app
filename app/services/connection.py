"""ConnectionManager — connect to 1C via COM, validate, and discover dropdown data.

Used by the GUI's connection screen ("Test connection") and the cell editor (live
warehouse / price-type dropdowns). The actual COM connection is created through an
injectable factory (default `engine.sources.com.Com1C`) so this is unit-testable with
a fake — no live 1C required.

All COM work here runs on whatever thread calls these methods; the GUI must call them
off the UI thread (Com1C does its own CoInitialize/CoUninitialize per call).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Callable, Protocol

from engine.config import ComConfig, OdataConfig
from engine.query_builder import (PRICE_TYPE_CATALOG, WAREHOUSE_CATALOG,
                                  build_catalog_names_query)
from engine.sources.com import Com1C
from engine.sources.odata import OdataPriceSource

from ..db.models import Connection1C

log = logging.getLogger(__name__)


class _Connection(Protocol):
    """What ConnectionManager needs from a connection object (Com1C satisfies this)."""
    def __enter__(self) -> "_Connection": ...
    def __exit__(self, *exc) -> bool: ...
    def query(self, text: str, columns: int) -> list[tuple]: ...


# factory(conn_string, progid) -> context manager yielding a _Connection
ConnectionFactory = Callable[..., _Connection]

# factory(OdataConfig) -> object with .probe()/.fetch_rows() (OdataPriceSource satisfies)
OdataSourceFactory = Callable[[OdataConfig], OdataPriceSource]


@dataclass
class ConnectionResult:
    ok: bool
    message: str       # human-readable (RU), safe to show in the UI
    detail: str = ""   # raw error text for logs/diagnostics (never a secret)


@dataclass
class Discovery:
    warehouses: list[str]
    price_types: list[str]


def _cs_quote(value: str) -> str:
    """Escape a value for a 1C connection string (double-quoted; embedded " doubled)."""
    return (value or "").replace('"', '""')


class ConnectionManager:
    def __init__(self, connection_factory: ConnectionFactory = Com1C,
                 odata_source_factory: OdataSourceFactory = OdataPriceSource) -> None:
        self._factory = connection_factory
        self._odata_source_factory = odata_source_factory

    # --- connection string ------------------------------------------------
    @staticmethod
    def build_conn_string(conn: Connection1C, password: str | None) -> str:
        pwd = _cs_quote(password or "")
        usr = _cs_quote(conn.usr)
        if conn.kind == "file":
            if not conn.file_path:
                raise ValueError("Для файловой базы укажите путь (file_path).")
            return f'File="{_cs_quote(conn.file_path)}";Usr="{usr}";Pwd="{pwd}";'
        # server base
        if not conn.srvr or not conn.ref:
            raise ValueError("Для серверной базы укажите сервер (srvr) и имя базы (ref).")
        return (f'Srvr="{_cs_quote(conn.srvr)}";Ref="{_cs_quote(conn.ref)}";'
                f'Usr="{usr}";Pwd="{pwd}";')

    def to_com_config(self, conn: Connection1C, password: str | None,
                      query: str) -> ComConfig:
        """Bridge to the engine: build a ComConfig for a price query (used by CellRunner)."""
        return ComConfig(progid=conn.progid,
                         conn_string=self.build_conn_string(conn, password),
                         query=query)

    def to_odata_config(self, conn: Connection1C, password: str | None) -> OdataConfig:
        """Bridge to the engine: build an OdataConfig from a saved OData connection.

        Credentials (usr/password) become HTTP Basic auth; the four query fields are
        the 1C OData entity-set requests. Field-name mappings keep OdataConfig's
        standard-configuration defaults.
        """
        return OdataConfig(
            base_url=conn.odata_base_url.strip().rstrip("/"),
            username=conn.usr,
            password=password or "",
            nomenclature_query=conn.odata_nomenclature_query.strip(),
            prices_query=conn.odata_prices_query.strip(),
            stock_query=conn.odata_stock_query.strip(),
            producers_query=conn.odata_producers_query.strip(),
            verify_ssl=conn.odata_verify_ssl,
        )

    # --- validate ---------------------------------------------------------
    def test_connection(self, conn: Connection1C, password: str | None) -> ConnectionResult:
        """Validate a connection (COM query or OData GET). Never raises — returns a result."""
        if conn.source == "odata":
            return self._test_odata(conn, password)
        try:
            cs = self.build_conn_string(conn, password)
        except ValueError as e:
            return ConnectionResult(False, str(e))
        try:
            with self._factory(cs, conn.progid) as c:
                c.query("ВЫБРАТЬ 1", 1)
            return ConnectionResult(True, "Соединение установлено.")
        except Exception as e:  # noqa: BLE001 - map every failure to a friendly message
            detail = error_text(e)
            log.warning("1C test_connection failed: %s", detail)
            return ConnectionResult(False, describe_1c_error(e), detail)

    def _test_odata(self, conn: Connection1C, password: str | None) -> ConnectionResult:
        if not conn.odata_base_url.strip():
            return ConnectionResult(False, "Укажите адрес OData-сервиса (base_url).")
        try:
            self._odata_source_factory(self.to_odata_config(conn, password)).probe()
            return ConnectionResult(True, "Соединение с OData установлено.")
        except Exception as e:  # noqa: BLE001 - map every failure to a friendly message
            detail = error_text(e)
            log.warning("OData test_connection failed: %s", detail)
            return ConnectionResult(False, describe_odata_error(e), detail)

    # --- discovery --------------------------------------------------------
    def discover(self, conn: Connection1C, password: str | None) -> Discovery:
        """Fetch warehouses + price types over ONE connection (raises on failure)."""
        cs = self.build_conn_string(conn, password)
        with self._factory(cs, conn.progid) as c:
            wh = c.query(build_catalog_names_query(WAREHOUSE_CATALOG, exclude_groups=True), 1)
            pt = c.query(build_catalog_names_query(PRICE_TYPE_CATALOG, exclude_groups=False), 1)
        return Discovery(warehouses=_names(wh), price_types=_names(pt))

    def list_warehouses(self, conn: Connection1C, password: str | None) -> list[str]:
        cs = self.build_conn_string(conn, password)
        with self._factory(cs, conn.progid) as c:
            rows = c.query(build_catalog_names_query(WAREHOUSE_CATALOG, exclude_groups=True), 1)
        return _names(rows)

    def list_price_types(self, conn: Connection1C, password: str | None) -> list[str]:
        cs = self.build_conn_string(conn, password)
        with self._factory(cs, conn.progid) as c:
            rows = c.query(build_catalog_names_query(PRICE_TYPE_CATALOG, exclude_groups=False), 1)
        return _names(rows)


def _names(rows: list[tuple]) -> list[str]:
    """First column of each row -> stripped name; drop blanks, dedupe preserving order."""
    seen: set[str] = set()
    out: list[str] = []
    for r in rows:
        name = str(r[0]).strip() if r and r[0] is not None else ""
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    return out


# Credential tokens in a 1C connection string. Some COM errors echo the whole
# connection string (incl. Pwd="...") in their message — redact before logging/showing.
# The quoted-value branch must consume DOUBLED quotes ("") because build_conn_string
# escapes an embedded " that way (Pwd="a""b"); a plain "[^"]*" would stop at the first
# inner quote and leak the rest of the secret.
_SECRET_RE = re.compile(r'(?i)\b(Pwd|Password|Usr|User)\s*=\s*("(?:[^"]|"")*"|[^;"\s]+)')


def _redact_secrets(text: str) -> str:
    return _SECRET_RE.sub(lambda m: f'{m.group(1)}="***"', text)


def error_text(exc: BaseException) -> str:
    """Flatten an exception (incl. pythoncom.com_error) into a single text blob.

    com_error.args = (hresult, msg, excinfo, argerr); excinfo holds the 1C
    description. The 1C password must never leak: if the underlying error text echoes
    the connection string, credential tokens (Pwd=/Usr=) are redacted here.
    """
    parts: list[str] = []
    for a in getattr(exc, "args", ()) or ():
        if isinstance(a, (tuple, list)):
            parts.extend(str(s) for s in a if isinstance(s, str))
        elif isinstance(a, str):
            parts.append(a)
        elif isinstance(a, int):
            parts.append(f"0x{a & 0xFFFFFFFF:08X}")
    parts.append(str(exc))
    return _redact_secrets(" | ".join(p for p in parts if p))


def describe_1c_error(exc: BaseException) -> str:
    """Map a 1C/COM failure to a friendly, actionable RU message.

    Substring heuristics — refine against real messages during live validation.
    """
    low = error_text(exc).lower()

    def has(*subs: str) -> bool:
        return any(s in low for s in subs)

    if has("внешнее соединение", "external connection"):
        return ("У пользователя 1С нет права «Внешнее соединение» (COM). "
                "Дайте это право или используйте другого пользователя.")
    if has("не зарегистрирована", "libnotregistered", "class not registered",
           "0x80040154", "0x80040153", "80040154"):
        return ("COM-коннектор 1С не зарегистрирован для этой разрядности. "
                "Запустите reference/register_typelib.py 64-битным Python (x64).")
    if has("идентификац", "пароль", "password", "имя пользователя",
           "пользователь не найден", "не найден пользователь"):
        return "Неверный логин или пароль пользователя 1С."
    if has("сервер", "server", "tcp", "не обнаружен", "недоступ", "timeout",
           "соединение с сервером", "rpc"):
        return "Сервер 1С недоступен. Проверьте адрес сервера, порты и сеть."
    return "Не удалось подключиться к 1С. Подробности — в журнале."


def describe_odata_error(exc: BaseException) -> str:
    """Map an OData/HTTP failure to a friendly, actionable RU message.

    Uses the HTTP status when the exception carries a response (requests.HTTPError),
    otherwise falls back to substring heuristics over the flattened text.
    """
    status = None
    resp = getattr(exc, "response", None)
    if resp is not None:
        status = getattr(resp, "status_code", None)
    if status in (401, 403):
        return ("OData отклонил авторизацию (%s): неверный логин/пароль или у "
                "пользователя нет прав на OData." % status)
    if status == 404:
        return ("OData вернул 404: неверный адрес сервиса (base_url) или имя "
                "публикации базы. Проверьте адрес.")
    if isinstance(status, int) and status >= 500:
        return "Сервер OData вернул ошибку (%s). Повторите позже." % status

    low = error_text(exc).lower()
    if any(s in low for s in ("certificate", "ssl", "self signed", "self-signed",
                              "cert_", "сертификат")):
        return ("Сертификат сервера не прошёл проверку. Для самоподписанного "
                "сертификата снимите галочку «Проверять SSL-сертификат» в настройках "
                "подключения OData.")
    if any(s in low for s in ("timeout", "timed out", "таймаут")):
        return ("OData не ответил вовремя (таймаут). Проверьте, что VPN (WireGuard) "
                "поднят, и адрес доступен.")
    if any(s in low for s in ("connection", "name or service", "getaddrinfo",
                              "не удалось разрешить", "соединени", "refused",
                              "max retries", "unreachable")):
        return ("Сервис OData недоступен. Проверьте, что VPN (WireGuard) подключён, и "
                "верны адрес (base_url) и порт.")
    return "Не удалось подключиться к OData. Подробности — в журнале."
