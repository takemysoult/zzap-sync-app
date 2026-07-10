"""PyInstaller entry point for the frozen app.

A plain script (PyInstaller can't freeze ``python -m app.gui`` directly). Delegates to
the real GUI entry so ``--minimized`` / ``--watchdog`` flags work in the exe too.

Также пишет САМЫЕ РАННИЕ отметки старта в ``logs/startup_trace.log`` — ещё до импорта
тяжёлых модулей (PySide2/apscheduler). Если перезапуск зависнет на импорте (как это было
при ложных срабатываниях сторожа — процесс не оставлял ни одной записи), в trace будет
видно: есть "entry" но нет "import done" ⇒ завис на импорте; есть "import done" но в
app.log нет "===старт===" ⇒ завис в начале main().
"""
import multiprocessing
import os
import sys
from datetime import datetime


def _early_trace(msg: str) -> None:
    try:
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") \
            or os.path.expanduser("~")
        d = os.path.join(base, "ZZapSync", "logs")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "startup_trace.log"), "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().isoformat(timespec='seconds')} "
                    f"pid={os.getpid()} argv={sys.argv[1:]} {msg}\n")
    except Exception:
        pass


if __name__ == "__main__":
    multiprocessing.freeze_support()   # MUST be first — prevents re-exec spawning a 2nd app
    _early_trace("entry: импортирую app.gui.app…")
    from app.gui.app import main
    _early_trace("import done: вызываю main()")
    sys.exit(main())
