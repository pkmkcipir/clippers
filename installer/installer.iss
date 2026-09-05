; Inno Setup script for AI Klipers.
; Build order: 1) python build.py  (produces dist/AI Klipers/)
;              2) open this file in Inno Setup (or `iscc installer.iss`)
;
; Download Inno Setup from https://jrsoftware.org/isinfo.php

#define MyAppName "AI Klipers"
#define MyAppVersion "0.1.0"
#define MyAppPublisher "AI Klipers"
#define MyAppExeName "AI Klipers.exe"

[Setup]
AppId={{B9F3E2B4-7B7B-4C1E-9E36-AIKLIPERSAPP}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
OutputDir=..\build
OutputBaseFilename=AI-Klipers-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; The app writes its DB/settings/logs to %APPDATA%, not Program Files,
; so it does not require admin rights to run after install.
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "indonesian"; MessagesFile: "compiler:Languages\Indonesian.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
; PyInstaller's onedir output -- everything under dist/AI Klipers/.
Source: "..\dist\AI Klipers\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Remove generated runtime files but leave the user's exported clips alone
; (those live under their chosen Output Folder from Settings, not here).
Type: filesandordirs; Name: "{app}\temp"
Type: filesandordirs; Name: "{app}\logs"
