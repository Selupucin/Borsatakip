; Borsa Bot — Inno Setup Installer
; Komut satırında:  iscc installer.iss
; Çıktı: installer_output\BorsaBot-Setup-X.Y.Z.exe

#define MyAppName "Borsa Bot"
#define MyAppVersion "0.1.3"
#define MyAppPublisher "Borsa Bot"
#define MyAppURL "https://github.com/"
#define MyAppExeName "BorsaBot.exe"

[Setup]
; AppId — benzersiz GUID; uninstall için kritik (değiştirme)
AppId={{B0F5A2E1-3D7C-4A8F-9B2D-1E4F8A6B3C2D}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
DefaultDirName={autopf}\BorsaBot
DefaultGroupName=Borsa Bot
DisableProgramGroupPage=yes
LicenseFile=
OutputDir=installer_output
OutputBaseFilename=BorsaBot-Setup-{#MyAppVersion}
SetupIconFile=app\resources\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/ultra
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
; Modern stil
ShowLanguageDialog=no

[Languages]
Name: "turkish"; MessagesFile: "compiler:Languages\Turkish.isl"

[Tasks]
Name: "desktopicon"; Description: "Masaüstüne kısayol oluştur"; GroupDescription: "Ek kısayollar:"; Flags: checkedonce
Name: "startupicon"; Description: "Windows başlangıcında otomatik çalıştır"; GroupDescription: "Ek seçenekler:"; Flags: unchecked

[Files]
; PyInstaller onedir çıktısının TÜM içeriği (BorsaBot klasörü)
Source: "dist\BorsaBot\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: ".env.example"; DestDir: "{app}"; Flags: ignoreversion
Source: "README.md"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist

[Icons]
; Başlat menüsü
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"

; Masaüstü kısayolu — Tasks ile koşullu
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

; Otomatik başlatma — Tasks ile koşullu
Name: "{userstartup}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"; Tasks: startupicon

[Run]
; Kurulum sonrası "Borsa Bot'u şimdi başlat" checkbox
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Uygulama klasöründeki user-generated dosyalar (varsa)
Type: filesandordirs; Name: "{app}\__pycache__"
; NOT: %LOCALAPPDATA%\BorsaBot SİLİNMEZ — kullanıcı verisi orada (DB, log).
; İstenirse aşağıdaki satırı aç:
; Type: filesandordirs; Name: "{localappdata}\BorsaBot"

[Code]
// ---------------------------------------------------------------------------
// PrepareToInstall: kurulum dosyaları kopyalanmadan ÖNCE çağrılır.
// Açık BorsaBot.exe ve QtWebEngine child process'lerini taskkill /T ile
// kapatırız — Inno Setup'ın kendi "CloseApplications" özelliği QtWebEngine
// gibi subprocess'leri kapatamadığı için file-locked hatası veriyordu.
// /T = process tree (BorsaBot.exe parent + tüm Chromium subprocess'ler).
// /F = force (kullanıcı dialog kapatamasa bile sonlandır).
// ---------------------------------------------------------------------------
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  NeedsRestart := False;
  // BorsaBot ve tüm child'larını kapat
  Exec(ExpandConstant('{cmd}'), '/C taskkill /F /IM BorsaBot.exe /T', '',
       SW_HIDE, ewWaitUntilTerminated, ResultCode);
  // Yedek: QtWebEngine subprocess'leri başka bir parent altında çalışıyor olabilir
  Exec(ExpandConstant('{cmd}'), '/C taskkill /F /IM QtWebEngineProcess.exe',
       '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  // Process tablosunun temizlenmesi için kısa gecikme (Windows handle release)
  Sleep(800);
  Result := '';  // boş = devam et
end;
