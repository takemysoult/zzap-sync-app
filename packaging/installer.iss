; Inno Setup script for ZZap Sync. Build (after PyInstaller) from the project root:
;   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" packaging\installer.iss
; Produces dist\ZZapSync-Setup-<ver>.exe

#define MyAppName "ZZap Sync"
#define MyAppVersion "1.0.0"
#define MyAppExe "ZZapSync.exe"

[Setup]
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=ZZap Sync
DefaultDirName={autopf}\ZZapSync
DefaultGroupName=ZZap Sync
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#SourcePath}..\dist
OutputBaseFilename=ZZapSync-Setup-{#MyAppVersion}
SetupIconFile={#SourcePath}zzapsync.ico
UninstallDisplayIcon={app}\{#MyAppExe}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; GroupDescription: "Дополнительно:"

[Files]
Source: "{#SourcePath}..\dist\ZZapSync\*"; DestDir: "{app}"; \
    Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\ZZap Sync"; Filename: "{app}\{#MyAppExe}"
Name: "{group}\Удалить ZZap Sync"; Filename: "{uninstallexe}"
Name: "{autodesktop}\ZZap Sync"; Filename: "{app}\{#MyAppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExe}"; Description: "Запустить ZZap Sync"; \
    Flags: nowait postinstall skipifsilent

[UninstallRun]
; Remove the per-user watchdog task and the autostart entry created by the app at runtime.
Filename: "{cmd}"; Parameters: "/c schtasks /delete /tn ""ZZapSync Watchdog"" /f"; \
    Flags: runhidden; RunOnceId: "DelWatchdogTask"
Filename: "{cmd}"; \
    Parameters: "/c reg delete ""HKCU\Software\Microsoft\Windows\CurrentVersion\Run"" /v ZZapSync /f"; \
    Flags: runhidden; RunOnceId: "DelAutostart"
