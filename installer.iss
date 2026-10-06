; Inno Setup script for the Haystacks installer.
; Built by build.ps1 and the release workflow, after PyInstaller:
;     iscc /DAppVersion=0.1.0 installer.iss
; Installs for the current user only (no administrator prompt), so the app's
; one-click updates can run this installer quietly (/SILENT).

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
; Never change AppId: Windows uses it to recognize updates of the same app.
AppId={{CB5E2048-96BE-48C7-81E1-8ABA3E666293}
AppName=Haystacks
AppVersion={#AppVersion}
AppVerName=Haystacks {#AppVersion}
AppPublisher=CyprianStream
AppPublisherURL=https://github.com/CyprianStream/Haystacks
AppSupportURL=https://github.com/CyprianStream/Haystacks/issues
AppUpdatesURL=https://github.com/CyprianStream/Haystacks/releases
DefaultDirName={localappdata}\Programs\Haystacks
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=dist
OutputBaseFilename=Haystacks-Setup-{#AppVersion}
SetupIconFile=assets\haystacks.ico
UninstallDisplayIcon={app}\Haystacks.exe
UninstallDisplayName=Haystacks
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
; Clear the previous version's program files so nothing stale is left behind.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "dist\Haystacks\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "LICENSE"; DestDir: "{app}"; DestName: "LICENSE.txt"; Flags: ignoreversion
Source: "THIRD-PARTY-LICENSES.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\Haystacks"; Filename: "{app}\Haystacks.exe"
Name: "{autodesktop}\Haystacks"; Filename: "{app}\Haystacks.exe"; Tasks: desktopicon

[Run]
; Also runs after a quiet (/SILENT) update, which restarts the app.
Filename: "{app}\Haystacks.exe"; Description: "{cm:LaunchProgram,Haystacks}"; Flags: nowait postinstall

[UninstallDelete]
; The downloaded speech engine (~800 MB). Settings in %APPDATA%\Haystacks,
; videos and transcripts are left alone.
Type: filesandordirs; Name: "{localappdata}\Haystacks\engine"
