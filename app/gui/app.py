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
    # Ignore unknown args (e.g. Qt's own) so autostart command quirks don't crash.
    args, _unknown = parser.parse_known_args(argv)
    return args


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    raw = argv if argv is not None else sys.argv[1:]
    args = _parse_args(raw)

    # Watchdog mode: no GUI — just check/relaunch and exit.
    if args.watchdog:
        from ..watchdog import run_watchdog
        return run_watchdog()

    from PySide6.QtWidgets import QApplication

    from ..single_instance import SingleInstance
    from .theme import apply_theme

    paths.ensure_dirs()
    app = QApplication(sys.argv)
    app.setApplicationName("ZZap Sync")
    app.setOrganizationName("ZZap Sync")
    app.setQuitOnLastWindowClosed(False)  # close-to-tray must not quit the app
    apply_theme(app)

    # Single instance: a second launch raises the running window and exits.
    single = SingleInstance()
    if not single.is_primary():
        single.ping_primary()
        log.info("ZZap Sync уже запущен — открываю существующее окно и выхожу.")
        return 0

    ctx = AppContext()
    service = SchedulerService(db_factory=ctx.new_db, work_dir=ctx.work_dir,
                               network_check=is_online)
    window = MainWindow(ctx, service)
    single.activated.connect(window.bring_to_front)
    window.start_background(minimized=args.minimized)
    try:
        return app.exec()
    finally:
        service.shutdown(wait=False)
        ctx.close()


if __name__ == "__main__":
    raise SystemExit(main())
