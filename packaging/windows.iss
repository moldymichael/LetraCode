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
[Code]
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  TestFile: String;
begin
  Result := '';
  TestFile := ExpandConstant('{app}\LetraCode.exe');
  if FileExists(TestFile) then
  begin
    { Renaming an open executable fails on Windows. Do not terminate a running
      chat or prompt Windows to reboot and replace locked files. }
    if not RenameFile(TestFile, TestFile + '.update-check') then
      Result := 'Close LetraCode before updating, then select Retry.'
    else if not RenameFile(TestFile + '.update-check', TestFile) then
      Result := 'Could not restore LetraCode.exe after checking the update. Close programs using the installation folder and retry.';
  end;
end;
