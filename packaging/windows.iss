; Compile through build-windows.py, which supplies version and absolute paths.
#ifndef AppVersion
  #error AppVersion must be supplied by build-windows.py
#endif

[Setup]
AppId=io.letracode.LetraCode
AppName=LetraCode
AppVersion={#AppVersion}
AppPublisher=LetraCode contributors
AppPublisherURL=https://github.com/moldymichael/LetraCode
AppSupportURL=https://github.com/moldymichael/LetraCode/issues
AppUpdatesURL=https://github.com/moldymichael/LetraCode/releases
AppMutex=Local\io.letracode.LetraCode.Running
DefaultDirName={localappdata}\Programs\LetraCode
DefaultGroupName=LetraCode
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
OutputDir={#OutputPath}
OutputBaseFilename=LetraCode-{#AppVersion}-windows-x64-setup
SetupIconFile={#IconPath}
UninstallDisplayIcon={app}\LetraCode.exe
LicenseFile={#SourcePath}\LICENSE
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=no
RestartApplications=no
SetupLogging=yes

[Files]
Source: "{#BundlePath}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userprograms}\LetraCode"; Filename: "{app}\LetraCode.exe"; WorkingDir: "{app}"

[Run]
Filename: "{app}\LetraCode.exe"; Description: "Start LetraCode"; Flags: nowait postinstall skipifsilent

; User data is outside {app}. Never add wildcard UninstallDelete rules here.
