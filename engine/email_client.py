"""Отправка готового XLSX-прайса письмом через SMTP (аналог zzap_client для почты).

Файл уходит вложением с ящика пользователя (EmailConfig.login) на адреса
EmailConfig.to_addrs. Ошибки делятся на постоянные и временные — по той же схеме,
что и у ZZap-клиента, поэтому CellRunner обрабатывает оба канала одинаково:

  - EmailPermanentError — неверный логин/пароль, отклонённый получатель/отправитель,
    5xx-отказ сервера. Повтор не поможет — нужна правка настроек (ERROR).
  - EmailTransientError — нет связи, таймаут, обрыв соединения, 4xx-ответ
    (лимит писем и т.п.). Файл откладывается в pending и досылается позже (FAIL).

ВАЖНО про пароли: Mail.ru, Яндекс и Gmail не принимают обычный пароль от почты по
SMTP — нужен «пароль приложения», создаваемый в настройках безопасности ящика.
Сообщение об ошибке аутентификации напоминает об этом.
"""
from __future__ import annotations

import logging
import mimetypes
import re
import smtplib
import socket
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Callable

from .config import EmailConfig

log = logging.getLogger(__name__)

# Таймаут SMTP-операций (connect/login/send), секунд.
SMTP_TIMEOUT = 120

_XLSX_MIME = ("application",
              "vnd.openxmlformats-officedocument.spreadsheetml.sheet")

DEFAULT_BODY = ("Здравствуйте!\n\n"
                "Актуальный прайс-лист во вложении.\n\n"
                "Письмо отправлено автоматически.")


class EmailError(RuntimeError):
    """Базовая ошибка отправки письма (текст уже на русском, можно показать в UI)."""


class EmailPermanentError(EmailError):
    """Невосстановимая ошибка: логин/пароль/адреса. Досыл не поможет — нужна
    правка настроек. CellRunner трактует её как ERROR (без откладывания)."""


class EmailTransientError(EmailError):
    """Временная ошибка: нет связи / таймаут / лимит сервера. CellRunner откладывает
    файл в pending и дошлёт позже (FAIL → RESEND_OK)."""


SmtpFactory = Callable[[EmailConfig], Any]


def parse_recipients(text: str) -> list[str]:
    """Разбирает строку получателей (запятая/точка с запятой/пробелы) в список адресов.

    Пустые куски отбрасываются, порядок и регистр сохраняются, дубликаты (без учёта
    регистра) убираются. Валидация минимальная — наличие «@» проверяет UI.
    """
    seen: set[str] = set()
    out: list[str] = []
    for part in re.split(r"[,;\s]+", text or ""):
        addr = part.strip()
        key = addr.lower()
        if addr and key not in seen:
            seen.add(key)
            out.append(addr)
    return out


def _default_smtp_factory(cfg: EmailConfig) -> Any:
    """Открывает SMTP-соединение по cfg.security ('ssl' -> SMTPS, иначе обычный SMTP)."""
    if cfg.security == "ssl":
        return smtplib.SMTP_SSL(cfg.smtp_host, cfg.smtp_port, timeout=SMTP_TIMEOUT)
    return smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=SMTP_TIMEOUT)


def _auth_error_text(host: str) -> str:
    return (f"Почтовый сервер {host} не принял логин/пароль. "
            "Для Mail.ru, Яндекс и Gmail нужен «пароль приложения» "
            "(создаётся в настройках безопасности почты) — обычный пароль от "
            "ящика по SMTP не работает.")


def _classify_smtp_error(e: Exception, host: str) -> EmailError:
    """Преобразует исключение smtplib/сети в постоянную или временную RU-ошибку."""
    if isinstance(e, smtplib.SMTPAuthenticationError):
        return EmailPermanentError(_auth_error_text(host))
    if isinstance(e, smtplib.SMTPRecipientsRefused):
        refused = ", ".join(e.recipients) if getattr(e, "recipients", None) else ""
        return EmailPermanentError(
            "Сервер отклонил адрес получателя"
            + (f": {refused}" if refused else "")
            + ". Проверьте адреса получателей в настройках ячейки.")
    if isinstance(e, smtplib.SMTPSenderRefused):
        code = getattr(e, "smtp_code", 550) or 550
        if 400 <= code < 500:
            return EmailTransientError(
                f"Сервер временно отклонил отправителя (код {code}) — возможно, "
                "превышен лимит писем. Повторим позже.")
        return EmailPermanentError(
            f"Сервер отклонил адрес отправителя (код {code}). Проверьте поле "
            "«Отправитель» — обычно оно должно совпадать с логином почты.")
    if isinstance(e, smtplib.SMTPResponseException):
        code = e.smtp_code or 0
        detail = _smtp_detail(e)
        if 400 <= code < 500:
            return EmailTransientError(
                f"Почтовый сервер вернул {code} — временная ошибка"
                f"{detail}. Повторим позже.")
        return EmailPermanentError(
            f"Почтовый сервер отклонил письмо (код {code}){detail}. "
            "Проверьте настройки почты.")
    if isinstance(e, (smtplib.SMTPServerDisconnected, socket.timeout,
                      TimeoutError, ConnectionError, OSError, smtplib.SMTPException)):
        return EmailTransientError(
            f"Нет связи с почтовым сервером {host} (таймаут или сеть недоступна). "
            "Повторим позже.")
    return EmailTransientError(f"Не удалось отправить письмо: {e}")


def _smtp_detail(e: smtplib.SMTPResponseException) -> str:
    error = e.smtp_error
    if isinstance(error, bytes):
        error = error.decode("utf-8", errors="replace")
    text = str(error or "").strip()
    return f". Ответ сервера: {text}" if text else ""


def build_message(cfg: EmailConfig, attachment: bytes | None = None,
                  file_name: str | None = None) -> EmailMessage:
    """Собирает письмо: тема/текст из cfg (или стандартные), XLSX вложением."""
    msg = EmailMessage()
    msg["From"] = cfg.from_addr or cfg.login
    msg["To"] = ", ".join(cfg.to_addrs)
    msg["Subject"] = cfg.subject.strip() or \
        f"Прайс-лист от {datetime.now():%d.%m.%Y %H:%M}"
    msg.set_content(cfg.body.strip() or DEFAULT_BODY)
    if attachment is not None:
        name = file_name or "price.xlsx"
        guessed, _ = mimetypes.guess_type(name)
        maintype, subtype = (guessed.split("/", 1) if guessed else _XLSX_MIME)
        msg.add_attachment(attachment, maintype=maintype, subtype=subtype,
                           filename=name)
    return msg


def _send(cfg: EmailConfig, msg: EmailMessage, smtp_factory: SmtpFactory | None) -> None:
    """Открывает соединение, логинится и отправляет письмо. Бросает Email*Error."""
    factory = smtp_factory or _default_smtp_factory
    try:
        client = factory(cfg)
    except Exception as e:  # noqa: BLE001 - любой сбой подключения -> временная ошибка
        raise EmailTransientError(
            f"Не удалось подключиться к почтовому серверу "
            f"{cfg.smtp_host}:{cfg.smtp_port}. Проверьте адрес сервера и порт; "
            "если сервер верный — повторим позже.") from e
    try:
        if cfg.security == "starttls":
            client.starttls()
        if cfg.login:
            client.login(cfg.login, cfg.password)
        refused = client.send_message(msg)
        # send_message бросает SMTPRecipientsRefused, только если отклонены ВСЕ
        # адреса; частичный отказ приходит словарём — тоже постоянная ошибка.
        if refused:
            raise EmailPermanentError(
                "Сервер отклонил часть получателей: "
                + ", ".join(refused) + ". Проверьте адреса в настройках ячейки.")
    except EmailError:
        raise
    except Exception as e:  # noqa: BLE001 - классифицируем на постоянную/временную
        raise _classify_smtp_error(e, cfg.smtp_host) from e
    finally:
        try:
            client.quit()
        except Exception:  # noqa: BLE001 - соединение могло уже закрыться
            pass


def send_price_email(cfg: EmailConfig, file_path: str | Path,
                     file_name: str | None = None, *,
                     smtp_factory: SmtpFactory | None = None) -> dict:
    """Отправляет XLSX-прайс вложением на cfg.to_addrs.

    `smtp_factory` — необязательная фабрика SMTP-клиента (для тестов). Возвращает
    словарь с итогом (по аналогии с upload_price), сетевые/серверные сбои — через
    EmailTransientError / EmailPermanentError.
    """
    if not cfg.to_addrs:
        raise EmailPermanentError("Не указан адрес получателя прайса.")
    file_path = Path(file_path)
    file_name = file_name or file_path.name
    file_bytes = file_path.read_bytes()

    msg = build_message(cfg, attachment=file_bytes, file_name=file_name)
    log.info("Почта: отправляю '%s' (%d Кб) с %s на %s",
             file_name, len(file_bytes) // 1024, cfg.login, ", ".join(cfg.to_addrs))
    _send(cfg, msg, smtp_factory)
    log.info("Почта: письмо отправлено (%s).", ", ".join(cfg.to_addrs))
    return {"success": True, "recipients": list(cfg.to_addrs)}


def send_test_email(cfg: EmailConfig, *,
                    smtp_factory: SmtpFactory | None = None) -> dict:
    """Отправляет короткое тестовое письмо (без вложения) — проверка входа в почту."""
    if not cfg.to_addrs:
        raise EmailPermanentError("Укажите адрес получателя тестового письма.")
    test_cfg = EmailConfig(
        smtp_host=cfg.smtp_host, smtp_port=cfg.smtp_port, login=cfg.login,
        password=cfg.password, to_addrs=list(cfg.to_addrs), security=cfg.security,
        from_addr=cfg.from_addr,
        subject=cfg.subject.strip() or "Тестовое письмо — рассылка прайса",
        body=cfg.body.strip() or ("Это тестовое письмо. Настройки почты работают: "
                                  "приложение сможет отправлять прайс-лист с этого "
                                  "ящика."))
    msg = build_message(test_cfg)
    _send(test_cfg, msg, smtp_factory)
    log.info("Почта: тестовое письмо отправлено (%s).", ", ".join(cfg.to_addrs))
    return {"success": True, "recipients": list(cfg.to_addrs)}
