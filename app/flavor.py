"""Флейвор сборки: одно и то же приложение собирается в двух вариантах.

  - 'zzap'  (по умолчанию) — «ZZap Sync»: прайс выгружается в ZZap (как всегда);
  - 'email' — «Рассылка прайса»: отдельное приложение, прайс уходит письмом.

Флейвор выбирается переменной окружения ``ZZAP_APP_FLAVOR`` (её выставляет frozen
entry point ДО импорта app.*; дочерние процессы планировщика наследуют окружение,
поэтому у них флейвор тот же). Оба варианта используют один движок и одну схему БД,
но полностью изолированы друг от друга как приложения: разные папки данных, имена
мьютекса одиночного экземпляра, задачи сторожа и записи автозапуска — их можно
ставить и запускать на одном ПК одновременно.

Каждый вариант показывает ТОЛЬКО свой канал доставки (см. ``forced_target``):
у «ZZap Sync» нет вкладки «Почта», у «Рассылки прайса» — вкладки кабинетов ZZap.
"""
from __future__ import annotations

import os

FLAVOR_ZZAP = "zzap"
FLAVOR_EMAIL = "email"
ENV_VAR = "ZZAP_APP_FLAVOR"


def flavor() -> str:
    """'zzap' | 'email' — читается из окружения при каждом вызове (тесты меняют env)."""
    value = (os.environ.get(ENV_VAR) or "").strip().lower()
    return FLAVOR_EMAIL if value == FLAVOR_EMAIL else FLAVOR_ZZAP


def is_email() -> bool:
    return flavor() == FLAVOR_EMAIL


def app_id() -> str:
    """Латинский идентификатор: имя папки данных, база имён мьютекса/задачи/реестра."""
    return "PriceMailer" if is_email() else "ZZapSync"


def display_name() -> str:
    """Имя приложения для пользователя (трей, уведомления, заголовки)."""
    return "Рассылка прайса" if is_email() else "ZZap Sync"


def window_title() -> str:
    return ("Рассылка прайса — отправка прайса 1С на почту" if is_email()
            else "ZZap Sync — синхронизация прайсов 1С → ZZap")


def db_filename() -> str:
    return "pricemailer.db" if is_email() else "zzapsync.db"


def tray_letter() -> str:
    """Буква на рисуемой в рантайме иконке трея."""
    return "@" if is_email() else "Z"


def forced_target() -> str:
    """Канал доставки ячеек этого флейвора ('zzap' | 'email').

    Каждый вариант приложения работает ровно с одним каналом — редактор ячейки
    не показывает выбор, а detach-поля другого канала обнуляются при сохранении.
    """
    return FLAVOR_EMAIL if is_email() else FLAVOR_ZZAP


def online_probe_host() -> tuple[str, int]:
    """Хост:порт для быстрой проверки «интернет есть?» перед отправкой.

    Для ZZap — хост его API; для почтового варианта — крупный почтовый хост
    (это лишь индикатор доступности сети, не гарантия доставки)."""
    return ("mail.ru", 443) if is_email() else ("b52-api.zzap.pro", 443)
