"""CellRunner с ячейками target='email' (без сети — источник и отправщик подменены).

Покрывает: успешную отправку (правильный EmailConfig из аккаунта+ячейки), сбой
отправки -> pending -> досылка, постоянную ошибку (неверный пароль) -> ERROR без
pending, 0 строк -> ERROR, отсутствие получателей/ящика -> ERROR, и что смешанный
run_all_enabled шлёт zzap-ячейку в ZZap, а почтовую — на почту.
"""
import pytest

from app.db.dal import Database
from app.db.models import Cabinet, Cell, Connection1C, EmailAccount
from app.services.cell_runner import (CellRunner, RUN_ERROR, RUN_FAIL, RUN_OK,
                                      RUN_RESEND_OK)
from conftest import FakeCipher, make_rows
from engine.config import EmailConfig
from engine.delivery import Delivery
from engine.email_client import EmailPermanentError, EmailTransientError


class FakeSource:
    def __init__(self, rows):
        self._rows = rows

    def fetch_rows(self):
        return list(self._rows)


class RecordingEmailSender:
    """Stand-in for engine.email_client.send_price_email; records calls, can raise."""

    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def __call__(self, cfg, file_path, file_name=None, **kwargs):
        self.calls.append({"cfg": cfg, "file_path": str(file_path),
                           "file_name": file_name})
        if self.error:
            raise self.error
        return {"success": True, "recipients": list(cfg.to_addrs)}


class RecordingUploader:
    def __init__(self):
        self.calls = []

    def __call__(self, cfg, file_path, file_name=None, *, session=None):
        self.calls.append({"cfg": cfg})
        return {"success": True, "result": {}}


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "app.db", cipher=FakeCipher())
    try:
        yield database
    finally:
        database.close()


def _seed_email_cell(db, *, enabled=True, password="app-pass",
                     email_to="client@example.com", subject="", account=True):
    conn = db.add_connection(
        Connection1C(name="base", kind="server", srvr="Serv1C", ref="ut2025",
                     usr="Натали"), password="p@ss")
    acc = None
    if account:
        acc = db.add_email_account(
            EmailAccount(name="Рабочая", smtp_host="smtp.mail.ru", smtp_port=465,
                         security="ssl", login="me@mail.ru"),
            password=password)
    cell_id = db.add_cell(Cell(
        name="Почтовая", enabled=enabled, connection_id=conn, cabinet_id=None,
        code_templ=0, price_type="ZZap", warehouses=["Квант (Новые) 3 этаж"],
        target="email", email_account_id=acc, email_to=email_to,
        email_subject=subject))
    return cell_id, conn, acc


def _runner(db, tmp_path, *, rows=None, sender=None, uploader=None):
    rows = make_rows() if rows is None else rows
    return CellRunner(db, tmp_path / "work",
                      source_factory=lambda cfg: FakeSource(rows),
                      uploader=uploader or RecordingUploader(),
                      email_sender=sender or RecordingEmailSender())


def test_email_cell_sends_with_account_config(db, tmp_path):
    cell_id, *_ = _seed_email_cell(
        db, email_to="client@example.com; boss@example.com", subject="Прайс дня")
    sender = RecordingEmailSender()
    res = _runner(db, tmp_path, sender=sender).run_cell(cell_id)

    assert res.status == RUN_OK
    assert res.posted is True
    assert res.rows_sent == 2                    # make_rows -> 2 после clean_rows
    assert "client@example.com" in res.message   # сообщение называет получателей
    assert len(sender.calls) == 1
    cfg = sender.calls[0]["cfg"]
    assert isinstance(cfg, EmailConfig)
    assert cfg.smtp_host == "smtp.mail.ru" and cfg.smtp_port == 465
    assert cfg.security == "ssl"
    assert cfg.login == "me@mail.ru" and cfg.password == "app-pass"
    assert cfg.from_addr == "me@mail.ru"         # пустой from_addr = логин
    assert cfg.to_addrs == ["client@example.com", "boss@example.com"]
    assert cfg.subject == "Прайс дня"
    run = db.list_runs(cell_id=cell_id)[0]
    assert run.status == RUN_OK and run.rows_sent == 2


def test_email_transient_failure_stages_pending_then_retry_resends(db, tmp_path):
    cell_id, *_ = _seed_email_cell(db)
    failing = RecordingEmailSender(error=EmailTransientError("Нет связи."))
    res = _runner(db, tmp_path, sender=failing).run_cell(cell_id)

    assert res.status == RUN_FAIL
    assert res.posted is False
    delivery = Delivery(tmp_path / "work" / f"cell_{cell_id}")
    assert delivery.get_pending() is not None

    ok = RecordingEmailSender()
    res2 = _runner(db, tmp_path, sender=ok).retry_pending(cell_id)
    assert res2.status == RUN_RESEND_OK
    assert res2.posted is True
    assert len(ok.calls) == 1                    # досылка ушла через почту
    assert isinstance(ok.calls[0]["cfg"], EmailConfig)
    assert delivery.get_pending() is None


def test_email_permanent_error_is_error_and_not_staged(db, tmp_path):
    cell_id, *_ = _seed_email_cell(db)
    bad = RecordingEmailSender(error=EmailPermanentError(
        "Сервер не принял логин/пароль."))
    res = _runner(db, tmp_path, sender=bad).run_cell(cell_id)

    assert res.status == RUN_ERROR
    assert len(bad.calls) == 1
    assert Delivery(tmp_path / "work" / f"cell_{cell_id}").get_pending() is None


def test_email_zero_rows_is_blocked(db, tmp_path):
    cell_id, *_ = _seed_email_cell(db)
    sender = RecordingEmailSender()
    res = _runner(db, tmp_path, rows=[], sender=sender).run_cell(cell_id)

    assert res.status == RUN_ERROR
    assert sender.calls == []
    assert "0 строк" in res.message


def test_email_cell_without_recipients_is_error(db, tmp_path):
    cell_id, *_ = _seed_email_cell(db, email_to="  ")
    sender = RecordingEmailSender()
    res = _runner(db, tmp_path, sender=sender).run_cell(cell_id)

    assert res.status == RUN_ERROR
    assert sender.calls == []
    assert "получател" in res.message.lower()


def test_email_cell_without_account_is_error(db, tmp_path):
    cell_id, *_ = _seed_email_cell(db, account=False)
    res = _runner(db, tmp_path).run_cell(cell_id)
    assert res.status == RUN_ERROR
    assert "ящик" in res.message.lower()


def test_email_cell_without_password_is_error_not_fail(db, tmp_path):
    # Нет пароля — это настройка, а не сеть: ERROR без pending.
    cell_id, *_ = _seed_email_cell(db, password=None)
    sender = RecordingEmailSender()
    res = _runner(db, tmp_path, sender=sender).run_cell(cell_id)

    assert res.status == RUN_ERROR
    assert sender.calls == []
    assert "пароль" in res.message.lower()
    assert Delivery(tmp_path / "work" / f"cell_{cell_id}").get_pending() is None


def test_offline_email_cell_stages_pending_without_send(db, tmp_path):
    cell_id, *_ = _seed_email_cell(db)
    sender = RecordingEmailSender()
    runner = CellRunner(db, tmp_path / "work",
                        source_factory=lambda cfg: FakeSource(make_rows()),
                        email_sender=sender, network_check=lambda: False)
    res = runner.run_cell(cell_id)

    assert res.status == RUN_FAIL
    assert sender.calls == []                    # ни одной попытки офлайн
    assert Delivery(tmp_path / "work" / f"cell_{cell_id}").get_pending() is not None


def test_mixed_run_all_routes_each_cell_to_its_channel(db, tmp_path):
    email_cell, conn, _acc = _seed_email_cell(db)
    cab = db.add_cabinet(Cabinet(name="Cab"), api_key="zzap1_key")
    zzap_cell = db.add_cell(Cell(
        name="ZZap", enabled=True, connection_id=conn, cabinet_id=cab,
        code_templ=330017019, price_type="ZZap", warehouses=["W"]))

    sender = RecordingEmailSender()
    uploader = RecordingUploader()
    results = _runner(db, tmp_path, sender=sender, uploader=uploader).run_all_enabled()

    assert {r.cell_id for r in results} == {email_cell, zzap_cell}
    assert all(r.status == RUN_OK for r in results)
    assert len(sender.calls) == 1                # почтовая ячейка -> письмо
    assert len(uploader.calls) == 1              # zzap-ячейка -> upload
