; Instalador de OpenLaser Studio para Windows (Inno Setup 6).
; Lo compila build_installer.bat a partir de dist\OpenLaserStudio (PyInstaller).
; Resultado: dist\OpenLaserStudio-0.6.0-setup.exe

#define AppName "OpenLaser Studio"
#define AppVersion "0.6.0"
#define AppExe "OpenLaserStudio.exe"
#define AppPublisher "OpenLaser Studio contributors"

[Setup]
AppId={{6E0B7C1A-5D2F-4E8B-9C33-0A51A7E2F6B4}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; Instalación para todos los usuarios (con permisos) o sólo para el usuario actual, a elegir.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=OpenLaserStudio-{#AppVersion}-setup
SetupIconFile=openlaser.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName} {#AppVersion}
LicenseFile=..\LICENSE
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
VersionInfoVersion={#AppVersion}.0
VersionInfoProductName={#AppName}

[Languages]
Name: "es"; MessagesFile: "compiler:Languages\Spanish.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
es.DesktopIcon=Crear un acceso directo en el &escritorio
en.DesktopIcon=Create a &desktop shortcut
es.Examples=Ejemplos de OpenLaser Studio
en.Examples=OpenLaser Studio examples
es.SafetyNote=OpenLaser Studio controla equipos láser. El programa no sustituye la parada de emergencia física, los interbloqueos ni la supervisión. Use siempre protección ocular adecuada.
en.SafetyNote=OpenLaser Studio controls laser equipment. The software does not replace the physical emergency stop, interlocks or supervision. Always wear suitable eye protection.

[Tasks]
Name: "desktopicon"; Description: "{cm:DesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\OpenLaserStudio\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\{cm:Examples}"; Filename: "{app}\_internal\examples"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[Code]
procedure InitializeWizard;
begin
  { Aviso de seguridad en la página de bienvenida }
  WizardForm.WelcomeLabel2.Caption := WizardForm.WelcomeLabel2.Caption + #13#10#13#10 + CustomMessage('SafetyNote');
end;
