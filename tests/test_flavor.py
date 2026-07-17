"""Флейворы 'zzap' / 'email' — два независимых приложения из одного кода.

Проверяется изоляция идентичности: папка данных, имя БД, мьютекс одиночного
экземпляра, задача сторожа, значение автозапуска — всё различается, чтобы оба
приложения могли жить на одном ПК одновременно и не мешать друг другу.
"""
from __future__ import annotations

import pytest

from app import flavor, paths
from app.services import watchdog_task
from app.services.autostart import app_value_name
from app.single_instance import default_key


@pytest.fixture
def email_flavor(monkeypatch):
    monkeypatch.setenv(flavor.ENV_VAR, "email")


@pytest.fixture
def zzap_flavor(monkeypatch):
    monkeypatch.delenv(flavor.ENV_VAR, raising=False)


def test_default_flavor_is_zzap(zzap_flavor):
    assert flavor.flavor() == "zzap"
    assert flavor.is_email() is False
    assert flavor.app_id() == "ZZapSync"
    assert flavor.display_name() == "ZZap Sync"
    assert flavor.forced_target() == "zzap"


def test_email_flavor_identity(email_flavor):
    assert flavor.flavor() == "email"
    assert flavor.is_email() is True
    assert flavor.app_id() == "PriceMailer"
    assert flavor.display_name() == "Рассылка прайса"
    assert flavor.forced_target() == "email"


def test_unknown_flavor_value_falls_back_to_zzap(monkeypatch):
    monkeypatch.setenv(flavor.ENV_VAR, "whatever")
    assert flavor.flavor() == "zzap"


def test_data_dirs_and_db_are_separate(monkeypatch, tmp_path):
    # Без ZZAP_APP_DATA override — путь строится от LOCALAPPDATA + имя флейвора.
    monkeypatch.delenv("ZZAP_APP_DATA", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    monkeypatch.delenv(flavor.ENV_VAR, raising=False)
    zzap_dir, zzap_db = paths.app_data_dir(), paths.db_path()
    monkeypatch.setenv(flavor.ENV_VAR, "email")
    mail_dir, mail_db = paths.app_data_dir(), paths.db_path()

    assert zzap_dir.name == "ZZapSync" and mail_dir.name == "PriceMailer"
    assert zzap_dir != mail_dir
    assert zzap_db.name == "zzapsync.db" and mail_db.name == "pricemailer.db"


def test_single_instance_keys_differ(monkeypatch):
    monkeypatch.delenv(flavor.ENV_VAR, raising=False)
    zzap_key = default_key()
    monkeypatch.setenv(flavor.ENV_VAR, "email")
    mail_key = default_key()
    assert zzap_key == "ZZapSyncSingleton"
    assert mail_key == "PriceMailerSingleton"
    assert zzap_key != mail_key


def test_watchdog_task_names_differ(monkeypatch):
    monkeypatch.delenv(flavor.ENV_VAR, raising=False)
    zzap_task = watchdog_task.task_name()
    monkeypatch.setenv(flavor.ENV_VAR, "email")
    mail_task = watchdog_task.task_name()
    assert zzap_task == "ZZapSync Watchdog"
    assert mail_task == "PriceMailer Watchdog"
    # схtasks-команды используют имя своего флейвора
    assert mail_task in watchdog_task.build_create_args("CMD")


def test_autostart_value_names_differ(monkeypatch):
    monkeypatch.delenv(flavor.ENV_VAR, raising=False)
    assert app_value_name() == "ZZapSync"
    monkeypatch.setenv(flavor.ENV_VAR, "email")
    assert app_value_name() == "PriceMailer"
