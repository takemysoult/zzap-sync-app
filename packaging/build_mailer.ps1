# Build PriceMailer.exe (PyInstaller) then the installer (Inno Setup) for
# «Рассылка прайса» — the standalone e-mail flavor. Run from anywhere:
#   powershell -ExecutionPolicy Bypass -File packaging\build_mailer.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# Python 3.10 + PySide2 venv: Qt5 runs on Win10 1607 (Qt6 needs 1809+).
$py = Join-Path $root ".venv310\Scripts\python.exe"

# The installer bundles the Microsoft VC++ runtime (Qt5/PySide2 needs it on clean PCs).
$redist = Join-Path $root "packaging\redist\vc_redist.x64.exe"
if (-not (Test-Path $redist)) {
    Write-Host "==> Downloading VC++ redistributable (vc_redist.x64.exe)"
    New-Item -ItemType Directory -Force (Split-Path $redist) | Out-Null
    & curl.exe -L --max-time 300 -o $redist "https://aka.ms/vs/17/release/vc_redist.x64.exe"
    if ((-not (Test-Path $redist)) -or ((Get-Item $redist).Length -lt 10000000)) {
        throw "VC redist download failed or file too small"
    }
}

Write-Host "==> PyInstaller (onedir PriceMailer.exe)"
& $py -m PyInstaller --noconfirm --clean "packaging\pricemailer.spec"
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed ($LASTEXITCODE)" }

$iscc = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if (-not (Test-Path $iscc)) { throw "ISCC.exe not found at $iscc" }
Write-Host "==> Inno Setup installer"
& $iscc "packaging\pricemailer.iss"
if ($LASTEXITCODE -ne 0) { throw "ISCC failed ($LASTEXITCODE)" }

Write-Host "==> Done. See dist\PriceMailer-Setup-*.exe"
