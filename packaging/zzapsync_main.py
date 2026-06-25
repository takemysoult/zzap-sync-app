"""PyInstaller entry point for the frozen app.

A plain script (PyInstaller can't freeze ``python -m app.gui`` directly). Delegates to
the real GUI entry so ``--minimized`` / ``--watchdog`` flags work in the exe too.
"""
import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()   # MUST be first — prevents re-exec spawning a 2nd app
    from app.gui.app import main
    sys.exit(main())
