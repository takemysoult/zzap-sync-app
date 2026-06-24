# Build ZZapSync.exe (PyInstaller) then the installer (Inno Setup).
# Run from anywhere:  powershell -ExecutionPolicy Bypass -File packaging\build.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$py = Join-Path $root ".venv\Scripts\python.exe"
Write-Host "==> PyInstaller (onedir ZZapSync.exe)"
& $py -m PyInstaller --noconfirm --clean "packaging\zzapsync.spec"
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed ($LASTEXITCODE)" }

$iscc = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if (-not (Test-Path $iscc)) { throw "ISCC.exe not found at $iscc" }
Write-Host "==> Inno Setup installer"
& $iscc "packaging\installer.iss"
if ($LASTEXITCODE -ne 0) { throw "ISCC failed ($LASTEXITCODE)" }

Write-Host "==> Done. See dist\ZZapSync-Setup-*.exe"
