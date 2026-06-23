"""Регистрирует typelib COM-коннектора 1С для текущего пользователя (без прав админа).

Лечит ошибку 'Библиотека не зарегистрирована' (TYPE_E_LIBNOTREGISTERED) при
вызове V83.COMConnector.Connect. Запускать 32-битным Python (.venv32):

    .venv32\\Scripts\\python.exe register_typelib.py
"""
import os
import sys
import winreg

import pythoncom

# CLSID коннектора V83.COMConnector (одинаков для всех версий 1С).
_CLSID = "{181E893D-73A4-4722-B61D-D604B3D67D47}"
# Запасной путь, если не выйдет прочитать из реестра.
_FALLBACK_DLL = r"C:\Program Files\1cv8\8.3.27.1989\bin\comcntr.dll"


def _registered_dll() -> str:
    """Путь к comcntr.dll из реестра — той разрядности, что и текущий Python.

    32-битный Python читает Wow6432Node-ветку, 64-битный — нативную, поэтому
    путь автоматически соответствует совместимому коннектору.
    """
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CLASSES_ROOT, rf"CLSID\{_CLSID}\InprocServer32")
        path, _ = winreg.QueryValueEx(key, None)
        winreg.CloseKey(key)
        if path and os.path.isfile(path):
            return path
    except OSError:
        pass
    return _FALLBACK_DLL


DLL = _registered_dll()


def _set(root, path, value, name=None):
    key = winreg.CreateKey(root, path)
    winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
    winreg.CloseKey(key)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    if not os.path.isfile(DLL):
        print(f"Не найден comcntr.dll: {DLL}")
        return 1

    print(f"Загружаю typelib из: {DLL}")
    tlb = pythoncom.LoadTypeLib(DLL)

    # 1) Лучший вариант — штатный API (если есть в этой сборке pywin32).
    if hasattr(pythoncom, "RegisterTypeLibForUser"):
        pythoncom.RegisterTypeLibForUser(tlb, DLL)
        print("OK: typelib зарегистрирован штатным RegisterTypeLibForUser (HKCU).")
        return 0

    # 2) Иначе — пишем ключи в HKCU\Software\Classes\TypeLib вручную.
    guid, lcid, _syskind, major, minor, flags = tlb.GetLibAttr()
    guid = str(guid)
    name = tlb.GetDocumentation(-1)[0] or "1C COMConnector"
    ver = f"{major:x}.{minor:x}"
    helpdir = os.path.dirname(DLL)
    base = rf"Software\Classes\TypeLib\{guid}\{ver}"
    root = winreg.HKEY_CURRENT_USER

    print(f"GUID={guid}  ver={ver}  lcid={lcid}  name={name}")
    _set(root, base, name)
    # win32-путь под нужный lcid и под нейтральный (0) — для надёжности.
    for lc in {lcid, 0}:
        _set(root, rf"{base}\{lc}\win32", DLL)
    _set(root, rf"{base}\FLAGS", str(flags))
    _set(root, rf"{base}\HELPDIR", helpdir)
    print("OK: typelib записан в HKCU\\Software\\Classes\\TypeLib.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
