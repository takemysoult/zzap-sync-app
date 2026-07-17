# PyInstaller spec for «Рассылка прайса» (PriceMailer) — the e-mail flavor of the app
# (onedir, windowed). Build from the project root with the Python 3.10 + PySide2 venv:
#   .venv310\Scripts\pyinstaller packaging\pricemailer.spec
# The flavor is fixed by the entry point (pricemailer_main.py sets ZZAP_APP_FLAVOR=email
# before importing app.*), so this bundle IS the separate mail app.
import os

ROOT = os.path.dirname(os.path.abspath(SPECPATH))   # project root (parent of packaging/)
ENTRY = os.path.join(SPECPATH, "pricemailer_main.py")
ICON = os.path.join(SPECPATH, "pricemailer.ico")
SCHEMA = os.path.join(ROOT, "app", "db", "schema.sql")

# schema.sql is read at runtime via Path(__file__).with_name(...) — must be bundled
# at app/db/schema.sql relative to the bundle root.
datas = [(SCHEMA, "app/db")]

# Pin lazily/dynamically imported modules PyInstaller's static analysis can miss.
hiddenimports = [
    "app.watchdog",
    "app.single_instance",
    "win32timezone",                       # pywin32/win32com runtime dep
    "PySide2.QtNetwork",                   # imported inside app.single_instance
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
    excludes=["tkinter", "pytest", "PySide6"],  # PySide2-only build — never mix Qt5/Qt6
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="PriceMailer",
    console=False,                          # windowed app (no console window)
    icon=ICON,
)
coll = COLLECT(exe, a.binaries, a.datas, name="PriceMailer")
