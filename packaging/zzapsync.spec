# PyInstaller spec for ZZap Sync (onedir, windowed). Build from the project root:
#   .venv\Scripts\pyinstaller packaging\zzapsync.spec
import os

ROOT = os.path.dirname(os.path.abspath(SPECPATH))   # project root (parent of packaging/)
ENTRY = os.path.join(SPECPATH, "zzapsync_main.py")
ICON = os.path.join(SPECPATH, "zzapsync.ico")
SCHEMA = os.path.join(ROOT, "app", "db", "schema.sql")

# schema.sql is read at runtime via Path(__file__).with_name(...) — must be bundled
# at app/db/schema.sql relative to the bundle root.
datas = [(SCHEMA, "app/db")]

# Pin lazily/dynamically imported modules PyInstaller's static analysis can miss.
hiddenimports = [
    "app.watchdog",
    "app.single_instance",
    "win32timezone",                       # pywin32/win32com runtime dep
    "apscheduler.triggers.interval",
    "apscheduler.triggers.date",
    "apscheduler.executors.pool",
    "apscheduler.jobstores.memory",
]

a = Analysis(
    [ENTRY],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="ZZapSync",
    console=False,                          # windowed app (no console window)
    icon=ICON,
)
coll = COLLECT(exe, a.binaries, a.datas, name="ZZapSync")
