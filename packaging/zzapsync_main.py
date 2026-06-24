"""PyInstaller entry point for the frozen app.

A plain script (PyInstaller can't freeze ``python -m app.gui`` directly). Delegates to
the real GUI entry so ``--minimized`` / ``--watchdog`` flags work in the exe too.
"""
import sys

from app.gui.app import main

if __name__ == "__main__":
    sys.exit(main())
