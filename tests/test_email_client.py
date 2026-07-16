"""engine.email_client — сборка письма, отправка через фейковый SMTP, таксономия ошибок.

Никакой реальной сети: SMTP-клиент подменяется через smtp_factory.
"""
import smtplib

import pytest
from openpyxl import Workbook

from engine.config import EmailConfig
from engine.email_client import (EmailPermanentError, EmailTransientError,
                                 parse_recipients, send_price_email,
                                 send_test_email)


class FakeSmtp:
    """Записывает starttls/login/send_message; может бросать на login/send."""

    def __init__(self, login_error=None, send_error=None, refused=None):
        self.calls = []
        self.login_error = login_error
        self.send_error = send_error
        self.refused = refused or {}
        self.quit_called = False

    def starttls(self):
        self.calls.append(("starttls",))

    def login(self, user, password):
        self.calls.append(("login", user, password))
        if self.login_error:
            raise self.login_error

    def send_message(self, msg):
        self.calls.append(("send", msg))
        if self.send_error:
            raise self.send_error
        return self.refused

    def quit(self):
        self.quit_called = True


def _cfg(**kwargs) -> EmailConfig:
    base = dict(smtp_host="smtp.mail.ru", smtp_port=465, login="me@mail.ru",
                password="app-password", to_addrs=["client@example.com"],
                security="ssl")
    base.update(kwargs)
    return EmailConfig(**base)


def _xlsx(tmp_path):
    path = tmp_path / "price.xlsx"
    wb = Workbook()
    wb.active.append(["BrandA", "A-1", "Деталь", 5, 100.0])
    wb.save(path)
    return path


def test_parse_recipients_splits_and_dedupes():
    assert parse_recipients("a@x.ru, b@y.ru;c@z.ru  A@X.RU") == \
        ["a@x.ru", "b@y.ru", "c@z.ru"]
    assert parse_recipients("") == []
    assert parse_recipients(None) == []


def test_send_price_email_builds_message_and_logs_in(tmp_path):
    client = FakeSmtp()
    cfg = _cfg(to_addrs=["client@example.com", "boss@example.com"],
               subject="Наш прайс")
    result = send_price_email(cfg, _xlsx(tmp_path), "price.xlsx",
                              smtp_factory=lambda c: client)

    assert result["success"] is True
    assert result["recipients"] == ["client@example.com", "boss@example.com"]
    assert ("login", "me@mail.ru", "app-password") in client.calls
    assert client.quit_called is True

    msg = next(c[1] for c in client.calls if c[0] == "send")
    assert msg["From"] == "me@mail.ru"
    assert msg["To"] == "client@example.com, boss@example.com"
    assert msg["Subject"] == "Наш прайс"
    attachments = list(msg.iter_attachments())
    assert len(attachments) == 1
    assert attachments[0].get_filename() == "price.xlsx"
    # XLSX как настоящий Excel-тип, не octet-stream
    assert attachments[0].get_content_type() == \
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def test_send_uses_starttls_when_configured(tmp_path):
    client = FakeSmtp()
    send_price_email(_cfg(security="starttls", smtp_port=587), _xlsx(tmp_path),
                     smtp_factory=lambda c: client)
    assert client.calls[0] == ("starttls",)


def test_default_subject_carries_date(tmp_path):
    client = FakeSmtp()
    send_price_email(_cfg(subject=""), _xlsx(tmp_path),
                     smtp_factory=lambda c: client)
    msg = next(c[1] for c in client.calls if c[0] == "send")
    assert msg["Subject"].startswith("Прайс-лист от ")


def test_auth_failure_is_permanent_and_mentions_app_password(tmp_path):
    client = FakeSmtp(login_error=smtplib.SMTPAuthenticationError(
        535, b"5.7.8 Authentication failed"))
    with pytest.raises(EmailPermanentError) as e:
        send_price_email(_cfg(), _xlsx(tmp_path), smtp_factory=lambda c: client)
    assert "пароль приложения" in str(e.value)
    assert client.quit_called is True            # соединение закрыто и при ошибке


def test_connect_failure_is_transient(tmp_path):
    def refuse(cfg):
        raise ConnectionRefusedError("refused")
    with pytest.raises(EmailTransientError):
        send_price_email(_cfg(), _xlsx(tmp_path), smtp_factory=refuse)


def test_all_recipients_refused_is_permanent(tmp_path):
    client = FakeSmtp(send_error=smtplib.SMTPRecipientsRefused(
        {"client@example.com": (550, b"no such user")}))
    with pytest.raises(EmailPermanentError) as e:
        send_price_email(_cfg(), _xlsx(tmp_path), smtp_factory=lambda c: client)
    assert "client@example.com" in str(e.value)


def test_partial_recipients_refused_is_permanent(tmp_path):
    client = FakeSmtp(refused={"boss@example.com": (550, b"no such user")})
    with pytest.raises(EmailPermanentError):
        send_price_email(_cfg(to_addrs=["a@x.ru", "boss@example.com"]),
                         _xlsx(tmp_path), smtp_factory=lambda c: client)


def test_5xx_response_is_permanent_4xx_is_transient(tmp_path):
    perm = FakeSmtp(send_error=smtplib.SMTPResponseException(552, b"message too big"))
    with pytest.raises(EmailPermanentError):
        send_price_email(_cfg(), _xlsx(tmp_path), smtp_factory=lambda c: perm)

    temp = FakeSmtp(send_error=smtplib.SMTPResponseException(451, b"try again later"))
    with pytest.raises(EmailTransientError):
        send_price_email(_cfg(), _xlsx(tmp_path), smtp_factory=lambda c: temp)


def test_disconnect_mid_send_is_transient(tmp_path):
    client = FakeSmtp(send_error=smtplib.SMTPServerDisconnected("closed"))
    with pytest.raises(EmailTransientError):
        send_price_email(_cfg(), _xlsx(tmp_path), smtp_factory=lambda c: client)


def test_no_recipients_is_permanent(tmp_path):
    with pytest.raises(EmailPermanentError):
        send_price_email(_cfg(to_addrs=[]), _xlsx(tmp_path),
                         smtp_factory=lambda c: FakeSmtp())


def test_send_test_email_sends_text_without_attachment():
    client = FakeSmtp()
    result = send_test_email(_cfg(), smtp_factory=lambda c: client)
    assert result["success"] is True
    msg = next(c[1] for c in client.calls if c[0] == "send")
    assert list(msg.iter_attachments()) == []
    assert "Тестовое письмо" in msg["Subject"]
