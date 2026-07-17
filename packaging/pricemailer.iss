; Inno Setup script for «Рассылка прайса» (PriceMailer) — the e-mail flavor,
; a SEPARATE app that can be installed alongside ZZap Sync. Build (after PyInstaller):
;   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" packaging\pricemailer.iss
; Produces dist\PriceMailer-Setup-<ver>.exe

#define MyAppName "Рассылка прайса"
#define MyAppVersion "1.0.0"
; (1.0.0 — первый выпуск: прайс из 1С письмом на почту по расписанию)
#define MyAppExe "PriceMailer.exe"

[Setup]
AppId=PriceMailer
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=PriceMailer
DefaultDirName={autopf}\PriceMailer
DefaultGroupName=Рассылка прайса
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#SourcePath}..\dist
OutputBaseFilename=PriceMailer-Setup-{#MyAppVersion}
SetupIconFile={#SourcePath}pricemailer.ico
UninstallDisplayIcon={app}\{#MyAppExe}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; GroupDescription: "Дополнительно:"

[Files]
Source: "{#SourcePath}..\dist\PriceMailer\*"; DestDir: "{app}"; \
    Flags: recursesubdirs createallsubdirs ignoreversion
; Microsoft Visual C++ runtime — Qt5/PySide2 needs it (VCRUNTIME/MSVCP + the Universal CRT).
Source: "{#SourcePath}redist\vc_redist.x64.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall

[Icons]
Name: "{group}\Рассылка прайса"; Filename: "{app}\{#MyAppExe}"
Name: "{group}\Удалить «Рассылка прайса»"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Рассылка прайса"; Filename: "{app}\{#MyAppExe}"; Tasks: desktopicon

[Run]
Filename: "{tmp}\vc_redist.x64.exe"; Parameters: "/install /quiet /norestart"; \
    StatusMsg: "Установка компонентов Microsoft Visual C++ (нужно один раз)…"; \
    Flags: waituntilterminated
Filename: "{app}\{#MyAppExe}"; Description: "Запустить «Рассылка прайса»"; \
    Flags: nowait postinstall skipifsilent

[UninstallRun]
; Remove the per-user watchdog task and the autostart entry created by the app at runtime.
Filename: "{cmd}"; Parameters: "/c schtasks /delete /tn ""PriceMailer Watchdog"" /f"; \
    Flags: runhidden; RunOnceId: "DelWatchdogTask"
Filename: "{cmd}"; \
    Parameters: "/c reg delete ""HKCU\Software\Microsoft\Windows\CurrentVersion\Run"" /v PriceMailer /f"; \
    Flags: runhidden; RunOnceId: "DelAutostart"
