"""Лёгкая проверка доступности сети (перед боевой отправкой в ZZap).

Phase 4 / офлайн-восстановление: чтобы не плодить лишние FAIL, когда интернета нет,
перед реальным POST делаем короткую TCP-проверку до хоста ZZap. Это НЕ гарантия, что
запрос пройдёт, а быстрый сигнал «сеть есть/нет»: если хост недоступен, файл просто
откладывается в pending и дошлётся проходом flush, как только связь вернётся.

`is_online` используется как `network_check` в CellRunner и в проходе досыла
SchedulerService. По умолчанию проверяется хост рабочего ZZap API (PROJECT_MEMORY §4).
"""
from __future__ import annotations

import logging
import socket

log = logging.getLogger(__name__)

DEFAULT_HOST = "b52-api.zzap.pro"
DEFAULT_PORT = 443
DEFAULT_TIMEOUT = 3.0


def is_online(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
              timeout: float = DEFAULT_TIMEOUT) -> bool:
    """True, если до ``host:port`` удаётся открыть TCP-соединение за ``timeout`` сек.

    Никогда не бросает исключение — любая ошибка сети/DNS трактуется как «офлайн».
    """
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError as e:  # DNS, отказ соединения, таймаут и т.п. — всё это «офлайн»
        log.debug("is_online(%s:%s) -> False: %s", host, port, e)
        return False
