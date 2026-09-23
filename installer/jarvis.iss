; Inno Setup script for Jarvis. Builds a single JarvisSetup.exe that:
;   - installs the PyInstaller-built dist\Jarvis folder (must exist already --
;     run build_exe.bat first)
;   - installs the Microsoft Edge WebView2 Runtime automatically if it's not
;     already on the target machine (the app's window needs it)
;   - creates Start Menu / optional Desktop shortcuts and a proper uninstaller
;   - does NOT ship a .env -- the app creates one itself on first run and
;     prompts for the Claude API key inside its own Settings window
;
; Build with: ISCC.exe installer\jarvis.iss  (from the project root)
; Output: installer\output\JarvisSetup.exe

#define MyAppName "Jarvis"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Jarvis"
#define MyAppExeName "Jarvis.exe"
#define MyLauncherName "run_jarvis.bat"

[Setup]
; Fixed AppId so re-running the installer later upgrades in place instead of
; creating a second Start Menu / Add-or-Remove-Programs entry.
AppId={{8F1B7C1E-9C2E-4C1A-9B7F-3E5D6A2F1D40}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; Runs without admin by default (installs to the current user's local
; Program Files-equivalent folder), but lets the user opt into an
; all-users/Program Files install via a checkbox if they run it elevated --
; this app writes its own config/.env/data files next to the exe at runtime,
; which is simplest under a location the running user can always write to.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=output
OutputBaseFilename=JarvisSetup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
SetupIconFile=jarvis.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Files]
; The entire PyInstaller onedir build, as produced by build_exe.bat.
Source: "..\dist\Jarvis\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; The WebView2 bootstrapper, run conditionally below -- not left behind.
Source: "MicrosoftEdgeWebview2Setup.exe"; DestDir: "{tmp}"; Flags: dontcopy

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyLauncherName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyLauncherName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{tmp}\MicrosoftEdgeWebview2Setup.exe"; Parameters: "/silent /install"; StatusMsg: "Installing Microsoft Edge WebView2 Runtime (required for the Jarvis window)..."; Check: NeedsWebView2; Flags: waituntilterminated
; No skipifsilent: this also fires for a silent self-update (see
; jarvis/updater.py's install_and_restart(), which runs this installer with
; /VERYSILENT then quits), so "Update ready -- Restart & Install" actually
; restarts Jarvis automatically instead of leaving it closed. Launches
; Jarvis.exe directly rather than run_jarvis.bat (whose trailing "press any
; key" prompt only makes sense for an interactive first run, not an
; unattended relaunch) -- see BUILD_EXE.md for why run_jarvis.bat is still
; what a manual first run should use instead.
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName} now"; Flags: nowait postinstall runasoriginaluser

[Code]
// Detects the WebView2 Runtime the same way Microsoft's own docs recommend:
// checking for the per-machine (HKLM) or per-user (HKCU) "pv" value under
// the runtime's registered Client GUID. If neither is present, the
// bootstrapper above is run to install it silently.
function NeedsWebView2(): Boolean;
var
  Version: String;
  Found: Boolean;
begin
  Found := False;
  if RegQueryStringValue(HKLM64, 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Version) then
    if (Version <> '') and (Version <> '0.0.0.0') then
      Found := True;
  if not Found then
    if RegQueryStringValue(HKLM, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Version) then
      if (Version <> '') and (Version <> '0.0.0.0') then
        Found := True;
  if not Found then
    if RegQueryStringValue(HKCU, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Version) then
      if (Version <> '') and (Version <> '0.0.0.0') then
        Found := True;
  Result := not Found;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssInstall then
  begin
    if NeedsWebView2() then
      ExtractTemporaryFile('MicrosoftEdgeWebview2Setup.exe');
  end;
end;
