; Instalador de CazaOfertas (Inno Setup). Lo compila GitHub Actions.
#define MyVersion GetEnv("APP_VERSION")

[Setup]
AppId=CazaOfertas-JuanCruzOrellano
AppName=CazaOfertas
AppVersion={#MyVersion}
AppVerName=CazaOfertas {#MyVersion}
AppPublisher=Juan Cruz Orellano
AppPublisherURL=https://github.com/JuanCruzOrellano/CazaOfertas
DefaultDirName={localappdata}\Programs\CazaOfertas
PrivilegesRequired=lowest
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableReadyPage=yes
OutputDir=dist
OutputBaseFilename=CazaOfertas-Setup
SetupIconFile=app.ico
UninstallDisplayIcon={app}\CazaOfertas.exe
UninstallDisplayName=CazaOfertas
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=force
RestartApplications=no
ShowLanguageDialog=no

[Languages]
Name: "es"; MessagesFile: "compiler:Languages\Spanish.isl"

[InstallDelete]
; La versión vieja (un solo .exe) y restos de versiones anteriores
Type: filesandordirs; Name: "{app}\_internal"
Type: files; Name: "{app}\version.txt"
Type: files; Name: "{app}\accesos.txt"

[Files]
Source: "dist\CazaOfertas\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userdesktop}\CazaOfertas"; Filename: "{app}\CazaOfertas.exe"
Name: "{userprograms}\CazaOfertas"; Filename: "{app}\CazaOfertas.exe"

[Run]
; Actualización automática (silenciosa): vuelve a abrir la app sin ventana nueva,
; la ventana que estaba abierta se recarga sola.
Filename: "{app}\CazaOfertas.exe"; Parameters: "--sin-ventana"; Flags: nowait; Check: WizardSilent
Filename: "{app}\CazaOfertas.exe"; Description: "Abrir CazaOfertas"; Flags: nowait postinstall; Check: not WizardSilent

[UninstallRun]
Filename: "schtasks"; Parameters: "/Delete /TN ""CazaOfertas - revisar precios"" /F"; Flags: runhidden; RunOnceId: "BorrarTarea"
