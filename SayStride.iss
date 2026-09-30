#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif

[Setup]
AppId=SayStride.Windows
AppName=SayStride
AppVersion={#AppVersion}
AppPublisher=SayStride
DefaultDirName={localappdata}\Programs\SayStride
DefaultGroupName=SayStride
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=release
OutputBaseFilename=SayStride-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\SayStride.exe
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Files]
Source: "dist-final\SayStride\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\SayStride"; Filename: "{app}\SayStride.exe"
Name: "{autodesktop}\SayStride"; Filename: "{app}\SayStride.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\SayStride.exe"; Description: "Launch SayStride and set up local speech"; Flags: nowait postinstall skipifsilent
