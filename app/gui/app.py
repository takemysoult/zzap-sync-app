"""GUI entry point: ``python -m app.gui``.

Creates the QApplication, the lifetime AppContext (real DB under %LOCALAPPDATA%,
DPAPI cipher), and the main window. Run from the project's 64-bit venv (1C COM
parity).
"""
from __future__ import annotations

import logging
import sys

from .. import paths
from .context import AppContext
from .main_window import MainWindow


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from PySide6.QtWidgets import QApplication

    paths.ensure_dirs()
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("ZZap Sync")
    app.setOrganizationName("ZZap Sync")

    ctx = AppContext()
    window = MainWindow(ctx)
    window.show()
    try:
        return app.exec()
    finally:
        ctx.close()


if __name__ == "__main__":
    raise SystemExit(main())
