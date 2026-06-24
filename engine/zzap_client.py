"""Загрузка готового XLSX в ZZap через REST API.

Метод (раздел «Прайс 1С», см. swagger b52-api.zzap.pro/swagger):
    POST {api_url}        # api_url = https://b52-api.zzap.pro/api/client/v1/price1c/upload
    Заголовок: zzap-api-key
    Тело (JSON): file_name, file_body (base64), code_templ, part_num=0, part_total=1
                 (part_* обязательны; части нумеруются с нуля, файл шлём одной частью)
Ответ: success, code, errors (result обычно пустой).

Файл грузится СТРОГО в шаблон с кодом code_templ — именно он направляет загрузку
в нужный шаблон вашего аккаунта. Отдельного «тестового режима» у метода нет —
безопасный режим (staging) реализуется в приложении (файл собираем, но не шлём).

ВНИМАНИЕ: payload и заголовки проверены реальной боевой загрузкой (PROJECT_MEMORY §4).
Не «чинить» part_num/part_total и имя заголовка.
"""
from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Any, Protocol

import requests

from .config import ZzapConfig

log = logging.getLogger(__name__)


class ZzapError(RuntimeError):
    """Базовая ошибка загрузки в ZZap (текст уже на русском, можно показать в UI)."""


class ZzapPermanentError(ZzapError):
    """Невосстановимая ошибка: ключ/права/адрес/содержимое. Досыл не поможет — нужна
    правка настроек или данных. CellRunner трактует её как ERROR (без откладывания)."""


class ZzapTransientError(ZzapError):
    """Временная ошибка: 5xx / 429 / таймаут / нет сети. CellRunner откладывает файл в
    pending и дошлёт позже (FAIL → RESEND_OK)."""


class _Poster(Protocol):
    """Минимальный интерфейс HTTP-клиента (requests.post или session.post).

    Позволяет в тестах подменить сеть, не трогая payload.
    """
    def __call__(self, url: str, *, json: Any, headers: dict, timeout: int) -> Any: ...


def _raise_for_zzap(resp: Any) -> None:
    """Преобразует HTTP-ответ ZZap с кодом ошибки в понятную RU-ошибку нужного типа.

    Постоянные (ZzapPermanentError) — повтор не поможет; временные
    (ZzapTransientError) — есть смысл дослать позже. Код HTTP оставляем в тексте для
    диагностики.
    """
    status = resp.status_code
    if status < 400:
        return
    text = (getattr(resp, "text", "") or "").strip()
    tail = f" Ответ сервера: {text}" if text else ""
    if status == 400:
        raise ZzapPermanentError(
            f"ZZap вернул 400 — неверные параметры запроса.{tail}")
    if status == 401:
        raise ZzapPermanentError(
            "ZZap вернул 401 — неверный или отсутствующий API-ключ (zzap-api-key). "
            "Проверьте ключ кабинета.")
    if status == 403:
        raise ZzapPermanentError(
            "ZZap вернул 403 — доступ запрещён: у ключа нет прав на этот метод/шаблон.")
    if status == 404:
        raise ZzapPermanentError(
            "ZZap вернул 404 — метод не найден. Проверьте адрес API (api_url) кабинета.")
    if status == 413:
        raise ZzapPermanentError(
            "ZZap вернул 413 — файл слишком большой для загрузки.")
    if status in (408, 429):
        raise ZzapTransientError(
            f"ZZap вернул {status} — превышен лимит запросов или таймаут. Повторим позже.")
    if status >= 500:
        raise ZzapTransientError(
            f"ZZap вернул {status} — сервер временно недоступен. Повторим позже.")
    # Прочие 4xx — считаем постоянной ошибкой (повтор вряд ли поможет).
    raise ZzapPermanentError(f"ZZap вернул {status} — запрос отклонён.{tail}")


def upload_price(cfg: ZzapConfig, file_path: str | Path, file_name: str | None = None,
                 *, session: requests.Session | None = None) -> dict:
    """Загружает XLSX в шаблон cfg.code_templ.

    session — необязательный requests.Session (или совместимый объект с .post)
    для переиспользования соединения и для мокинга в тестах. По умолчанию
    используется requests.post.
    """
    file_path = Path(file_path)
    file_name = file_name or file_path.name

    file_bytes = file_path.read_bytes()
    file_body = base64.b64encode(file_bytes).decode("ascii")

    payload = {
        "file_name": file_name,
        "file_body": file_body,
        "code_templ": cfg.code_templ,
        # Метод price1c/upload поддерживает многочастную загрузку и требует эти поля.
        # Части нумеруются с нуля (part_num=0); файл шлём целиком одной частью.
        "part_num": 0,
        "part_total": 1,
    }
    headers = {"zzap-api-key": cfg.api_key}

    log.info("ZZap: отправляю '%s' (%d Кб) в шаблон code_templ=%s",
             file_name, len(file_bytes) // 1024, cfg.code_templ)

    post: _Poster = session.post if session is not None else requests.post
    try:
        resp = post(cfg.api_url, json=payload, headers=headers, timeout=180)
    except requests.RequestException as e:
        # Нет связи / таймаут / DNS — временная ошибка, файл уйдёт в досыл.
        raise ZzapTransientError(
            "Нет связи с ZZap (таймаут или сеть недоступна). Повторим позже.") from e

    _raise_for_zzap(resp)   # 4xx/5xx -> ZzapPermanentError / ZzapTransientError

    try:
        data = resp.json()
    except ValueError:
        data = {"raw": resp.text}

    # Формат ответа: success / result.file_url / errors. success=false — содержимое
    # отвергнуто сервером (повтор не поможет) -> постоянная ошибка.
    if isinstance(data, dict) and data.get("success") is False:
        raise ZzapPermanentError(f"ZZap отклонил загрузку: {data.get('errors') or data}")

    result = data.get("result", {}) if isinstance(data, dict) else {}
    file_url = result.get("file_url") if isinstance(result, dict) else None
    log.info("ZZap: загрузка принята. Ссылка на файл: %s", file_url or data)
    return data
