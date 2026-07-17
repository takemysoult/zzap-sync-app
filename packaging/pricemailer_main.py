"""PyInstaller entry point for the frozen «Рассылка прайса» app (flavor 'email').

Ставит флейвор В САМОМ НАЧАЛЕ — до импорта любого модуля app.* — чтобы вся
идентичность (папка данных, мьютекс, сторож, автозапуск, вкладки) была почтовой.
Дочерние процессы (--run-all и т.п.) наследуют переменную окружения, поэтому
работают в том же флейворе автоматически.

Также пишет самые ранние отметки старта в ``logs/startup_trace.log`` — см.
zzapsync_main.py (та же диагностика зависаний на старте).
"""
import multiprocessing
import os
import sys
from datetime import datetime

os.environ["ZZAP_APP_FLAVOR"] = "email"   # ДО импорта app.* (см. app/flavor.py)


def _early_trace(msg: str) -> None:
    try:
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") \
            or os.path.expanduser("~")
        d = os.path.join(base, "PriceMailer", "logs")
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
