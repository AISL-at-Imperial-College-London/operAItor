; Inno Setup script: OperAItor-Setup-<version>.exe from the PyInstaller folder (packaging/windows/operaitor.spec).
;   iscc /DAppVersion=1.0.0 /DSourceDir=<path to the OperAItor folder> /DOutputDir=<folder> packaging\windows\installer.iss
; Installs for the current user only (no administrator rights), adds Start-menu and optional desktop
; shortcuts, and an uninstaller. Reports and settings in %LOCALAPPDATA%\OperAItor are kept on uninstall.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\..\dist\OperAItor"
#endif
#ifndef OutputDir
  #define OutputDir "..\..\dist"
#endif

[Setup]
AppId={{BA1A659D-6F28-4E53-A775-DF30EEFA56E0}
AppName=OperAItor
AppVersion={#AppVersion}
AppVerName=OperAItor {#AppVersion}
AppPublisher=AISL, Imperial College London
AppPublisherURL=https://github.com/AISL-at-Imperial-College-London/operAItor
AppSupportURL=https://github.com/AISL-at-Imperial-College-London/operAItor/issues
DefaultDirName={localappdata}\Programs\OperAItor
DefaultGroupName=OperAItor
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir={#OutputDir}
OutputBaseFilename=OperAItor-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName=OperAItor {#AppVersion}
LicenseFile=..\..\LICENSE

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\OperAItor"; Filename: "{app}\OperAItor.exe"; Comment: "Simulated acetone plant with AI operator agents"
Name: "{group}\Uninstall OperAItor"; Filename: "{uninstallexe}"
Name: "{autodesktop}\OperAItor"; Filename: "{app}\OperAItor.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\OperAItor.exe"; Description: "Start OperAItor now"; Flags: nowait postinstall skipifsilent
