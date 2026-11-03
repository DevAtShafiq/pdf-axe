; Office Axe — Windows Setup (Inno Setup 6)
;
; Build (from the repo root, after PyInstaller; SourceDir is relative to this file):
;   ISCC.exe /DSourceDir="..\dist\build-YYYYMMDD-HHMM\OfficeAxe" /DAppVersion=2026.11.3 installer\OfficeAxe.iss
; Output: installer\Output\OfficeAxe-Setup.exe
;
; Installs the whole PyInstaller folder (OfficeAxe.exe + _internal). An installed
; copy keeps its settings and temp files in %LOCALAPPDATA%\OfficeAxe (app_paths.py),
; since Program Files isn't writable.

#ifndef SourceDir
  #define SourceDir "..\dist\OfficeAxe"
#endif
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

#define AppName "Office Axe"
#define AppExe  "OfficeAxe.exe"

[Setup]
; Never change AppId: upgrades and the uninstaller find the app by it.
AppId={{6F3B2C1E-8A4D-4F7B-9C2E-0A5D7E31B9C4}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Office Axe
AppPublisherURL=https://officeaxe-8a44f.web.app
VersionInfoVersion={#AppVersion}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog commandline
OutputDir=Output
OutputBaseFilename=OfficeAxe-Setup
SetupIconFile=..\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
; _sfm_temp is scratch space from a test run, never shipped.
Source: "{#SourceDir}\*"; DestDir: "{app}"; Excludes: "_sfm_temp,sfm_settings.json,sfm_debug.log,.env"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
