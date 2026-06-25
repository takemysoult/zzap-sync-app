"""GUI entry point: ``python -m app.gui`` (``--minimized`` to start in the tray).

Creates the QApplication, the lifetime AppContext (real DB under %LOCALAPPDATA%,
DPAPI cipher), the Phase 4 SchedulerService (background uploads + offline recovery),
and the main window. Run from the project's 64-bit venv (1C COM parity).

``--watchdog`` runs the external crash-watchdog check and exits (no GUI). Otherwise a
single-instance guard keeps only one app running; a second launch just raises the
existing window. ``setQuitOnLastWindowClosed(False)`` keeps the app alive when the
window is closed to the tray; the tray «Выход» quits explicitly.
"""
from __future__ import annotations

import argparse
import logging
import multiprocessing
import os
import sys

from .. import paths
from ..logging_setup import setup_logging
from ..services.net import is_online
from ..services.scheduler import SchedulerService
from .context import AppContext
from .main_window import MainWindow

log = logging.getLogger(__name__)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="app.gui", add_help=True)
    parser.add_argument("--minimized", action="store_true",
                        help="запуститься свёрнутым в системный трей (для автозапуска)")
    parser.add_argument("--watchdog", action="store_true",
                        help="разовая проверка сторожа (перезапуск при падении) и выход")
    parser.add_argument("--run-all", action="store_true",
                        help="разовая выгрузка всех включённых ячеек и выход (дочерний процесс)")
    parser.add_argument("--run-cell", type=int, default=None, metavar="ID",
                        help="разовая выгрузка одной ячейки по id и выход (дочерний процесс)")
    # Ignore unknown args (e.g. Qt's own) so autostart command quirks don't crash.
    args, _unknown = parser.parse_known_args(argv)
    return args


def _run_headless(args: argparse.Namespace) -> int:
    """Дочерний режим: выполнить ОДНУ выгрузку и выйти (без Qt/трея/одиночного экземпляра).

    Планировщик запускает выгрузку именно так — в коротком дочернем процессе: после выхода
    ОС освобождает среду 1С COM (~400 МБ) и все хэндлы, а нативное зависание COM не может
    заморозить GUI (родитель убивает зависший процесс по таймауту). Результаты пишутся в
    run_history — родитель их оттуда и читает.
    """
    from ..services.cell_runner import CellRunner
    paths.ensure_dirs()
    ctx = AppContext()
    try:
        ctx.db.finalize_orphan_runs()
        runner = CellRunner(ctx.db, ctx.work_dir, network_check=is_online)
        if args.run_cell is not None:
            log.info("Дочерний процесс: выгрузка ячейки #%s.", args.run_cell)
            runner.run_cell(args.run_cell)
        else:
            log.info("Дочерний процесс: выгрузка всех включённых ячеек.")
            runner.run_all_enabled()
    except Exception as e:  # noqa: BLE001 - не падать молча; результат уже в run_history
        log.exception("Дочерняя выгрузка завершилась с ошибкой: %s", e)
        return 1
    finally:
        ctx.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    # Frozen-app guard: stop a re-executed child (e.g. via multiprocessing) from
    # starting a second copy of the GUI.
    multiprocessing.freeze_support()
    setup_logging()
    raw = argv if argv is not None else sys.argv[1:]
    args = _parse_args(raw)

    # Watchdog mode: no GUI — just check/relaunch and exit.
    if args.watchdog:
        from ..watchdog import run_watchdog
        return run_watchdog()

    # Headless run (child process launched by the scheduler): one sync, then exit.
    # Handled before QApplication / single-instance so it never opens a window and is
    # not blocked by the guard (the running GUI is the primary instance).
    if args.run_all or args.run_cell is not None:
        return _run_headless(args)

    # Диагностика крашей. Страховку старта взводим САМОЙ ПЕРВОЙ — до тяжёлых импортов
    # (Qt) — чтобы зависание на старте (например, сразу после загрузки ПК) оставило дамп
    # стеков в logs/stall.log. Хлебные крошки ниже показывают, до какого шага дошёл старт.
    from .. import diagnostics
    diagnostics.arm_startup_guard()
    log.info("=== ZZap Sync GUI старт: pid=%s frozen=%s minimized=%s ===",
             os.getpid(), getattr(sys, "frozen", False), args.minimized)
    diagnostics.start_resource_sampler()

    log.info("Старт: импортирую Qt…")
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from ..single_instance import SingleInstance
    from .theme import apply_theme

    paths.ensure_dirs()
    log.info("Старт: создаю QApplication…")
    app = QApplication(sys.argv)
    app.setApplicationName("ZZap Sync")
    app.setOrganizationName("ZZap Sync")
    app.setQuitOnLastWindowClosed(False)  # close-to-tray must not quit the app
    apply_theme(app)

    # Пульс с UI-потока раз в секунду — если он замолчит, детектор снимет дампы.
    diagnostics.install_stall_detector()
    _pulse_timer = QTimer(app)
    _pulse_timer.setInterval(1000)
    _pulse_timer.timeout.connect(diagnostics.pulse)
    _pulse_timer.start()

    # Single instance: a second launch raises the running window and exits.
    log.info("Старт: проверяю единственный экземпляр…")
    single = SingleInstance()
    if not single.is_primary():
        single.ping_primary()
        log.info("ZZap Sync уже запущен — открываю существующее окно и выхожу.")
        return 0

    log.info("Старт: открываю базу и планировщик…")
    ctx = AppContext()
    ctx.db.finalize_orphan_runs()   # clean up runs interrupted by a previous crash
    service = SchedulerService(db_factory=ctx.new_db, work_dir=ctx.work_dir,
                               network_check=is_online)
    window = MainWindow(ctx, service)
    single.activated.connect(window.bring_to_front)
    log.info("Старт: запускаю фоновый режим (трей/расписание)…")
    window.start_background(minimized=args.minimized)
    diagnostics.disarm_startup_guard()   # старт успешен — снимаем страховку
    log.info("Старт завершён — вхожу в цикл событий.")
    try:
        return app.exec()
    finally:
        service.shutdown(wait=False)
        ctx.close()


if __name__ == "__main__":
    raise SystemExit(main())
