"""Соак-тест пути 1С COM (ТОЛЬКО ЧТЕНИЕ — НИЧЕГО НЕ ОТПРАВЛЯЕТСЯ В ZZAP).

Цель: воспроизвести и измерить предполагаемую утечку/зависание, которые в проде
проявлялись как «приложение перестало отвечать через несколько часов» + события
RADAR_PRE_LEAK_64 в журнале Windows. Подозреваемый участок — повторные COM-запросы к
1С с РАБОЧЕГО потока (как это делает APScheduler), а не с главного.

Что делает: берёт реальное подключение 1С и ячейку из БД приложения, и гоняет
ИМЕННО `ComPriceSource.fetch_rows()` (сборку строк прайса) в цикле на ФОНОВОМ потоке,
снимая после каждой итерации RSS/GDI/USER/хэндлы/потоки. Никакой сборки XLSX-файла на
отправку и никакого POST в ZZap не происходит. Активен детектор зависаний: если
fetch_rows зависнет, в logs/stall.log попадёт дамп стеков всех потоков.

Запуск (из корня проекта, 64-битным venv):
    .venv\\Scripts\\python -m tools.soak_com [итераций] [--main-thread]

По умолчанию 60 итераций на фоновом потоке. Флаг --main-thread — прогон на главном
потоке (для сравнения поведения COM-апартамента).
"""
from __future__ import annotations

import os
import sys
import threading
import time

# Соак-тест работает с РЕАЛЬНЫМИ данными приложения (%LOCALAPPDATA%\ZZapSync), если не
# переопределено заранее, — берём то же подключение, что и боевой запуск.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import diagnostics  # noqa: E402
from app.gui.context import AppContext  # noqa: E402
from app.services.connection import ConnectionManager  # noqa: E402
from engine.query_builder import build_price_query  # noqa: E402
from engine.sources.com import ComPriceSource  # noqa: E402


def _pick_target(db):
    """Вернёт (conn, password, query, описание) для боевой ячейки или запасной вариант."""
    cells = db.list_cells()
    cell = next((c for c in cells if c.enabled and c.connection_id), None) \
        or next((c for c in cells if c.connection_id), None)
    if cell is not None:
        conn = db.get_connection(cell.connection_id)
        pwd = db.get_connection_password(conn.id)
        query = build_price_query(cell.warehouses, cell.price_type)
        return conn, pwd, query, f"ячейка #{cell.id} «{cell.name}» (вид цены: {cell.price_type})"
    # Запасной вариант: подключение по умолчанию + тривиальный запрос.
    conn = db.get_default_connection()
    if conn is None:
        raise SystemExit("В БД нет ни одной ячейки/подключения 1С — нечего гонять.")
    pwd = db.get_connection_password(conn.id)
    return conn, pwd, "ВЫБРАТЬ 1", f"подключение по умолчанию «{conn.name}» (тривиальный запрос)"


def main(argv: list[str]) -> int:
    iterations = next((int(a) for a in argv if a.isdigit()), 60)
    main_thread = "--main-thread" in argv

    # Детектор зависаний: порог должен превышать нормальное время запроса (4000+ строк
    # из 1С — несколько секунд). 60с с запасом; дамп уйдёт в logs/stall.log.
    os.environ.setdefault("ZZAP_DIAG_STALL_SECONDS", "60")
    diagnostics.install_stall_detector()

    ctx = AppContext()
    conn, pwd, query, desc = _pick_target(ctx.db)
    comcfg = ConnectionManager().to_com_config(conn, pwd, query)

    print(f"СОАК COM (только чтение): {desc}")
    print(f"Поток: {'главный' if main_thread else 'фоновый (как APScheduler)'}; "
          f"итераций: {iterations}")
    print("ВНИМАНИЕ: отправки в ZZAP НЕТ — только запрос 1С.\n")
    base = diagnostics.sample()
    print(f"старт      {diagnostics.format_sample(base)}")

    samples: list[dict] = []
    stop = {"err": None}

    def worker() -> None:
        for i in range(1, iterations + 1):
            diagnostics.pulse()  # «жив» — если fetch_rows зависнет, пульс устареет
            t0 = time.monotonic()
            try:
                rows = ComPriceSource(comcfg).fetch_rows()
            except Exception as e:  # noqa: BLE001
                stop["err"] = e
                print(f"итер {i:>3}: ОШИБКА запроса: {type(e).__name__}: {e}")
                return
            dt = time.monotonic() - t0
            s = diagnostics.sample()
            samples.append(s)
            print(f"итер {i:>3}: строк={len(rows):>5} {dt:5.1f}с  "
                  f"{diagnostics.format_sample(s)}")

    if main_thread:
        worker()
    else:
        t = threading.Thread(target=worker, name="zzap-soak-worker")
        t.start()
        t.join()

    print()
    if samples:
        first, last = samples[0], samples[-1]
        print("ИТОГ (рост за прогон, первая → последняя итерация):")
        for k in ("rss_mb", "gdi", "user", "handles", "threads"):
            print(f"  {k:>8}: {first[k]} → {last[k]}  (Δ {last[k] - first[k]})")
        leaking = (last["handles"] - first["handles"] > iterations // 2
                   or last["gdi"] - first["gdi"] > iterations // 2
                   or last["rss_mb"] - first["rss_mb"] > 100)
        print("\nВЕРДИКТ:", "ПОХОЖЕ НА УТЕЧКУ — растёт монотонно." if leaking
              else "стабильно — заметной утечки на этом пути не видно.")
    ctx.close()
    return 1 if stop["err"] else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
