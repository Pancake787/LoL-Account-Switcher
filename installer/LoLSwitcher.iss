; LoLSwitcher.iss — Inno Setup 6 script for LoL Account Switcher
;
; This is a BUILD HELPER, analogous to LoLSwitcher.spec — it belongs in the
; public repo (D-06) but produces no output until it is COMPILED. It is
; authored here in Plan 06-05 (Wave 1, before the orphan-commit snapshot in
; 06-03) and COMPILED in Plan 06-04 (Wave 3, after the PyInstaller --onedir
; build exists at dist\LoLSwitcher\).
;
; Build command (from project root, after `pyinstaller LoLSwitcher.spec`):
;   ISCC installer\LoLSwitcher.iss
;
; Produces: LoLSwitcher-v2.0.0-Setup.exe (at project root, see OutputDir below)
;
; This script wraps the existing --onedir output 1:1 — no application code
; changes. --onefile stays FORBIDDEN (COMMON-3 in RESEARCH.md); this is only
; an additional packaging step around the already-existing --onedir tree.
;
; The produced Setup.exe is UNSIGNED — no code-signing certificate has been
; purchased for this project. The same Windows SmartScreen "Windows protected
; your PC" warning that applies to the portable .exe also applies to this
; installer (documented in README.md / README.de.md, D-14).
;
; SourceDir is set to ".." below: Inno Setup resolves a relative SourceDir
; against the directory CONTAINING this script (installer\), so ".." resolves
; to the project root — every relative Source/OutputDir path in this script
; is therefore relative to the project root, not to installer\.

[Setup]
SourceDir=..

; Fixed AppId GUID — THIS MUST NEVER CHANGE ACROSS RELEASES.
; Changing it causes Windows to treat a future version as a completely
; different product, breaking upgrade detection and uninstall for existing
; users (D-14 upgrade stability). Generated once for this project; keep as-is
; forever.
AppId={{1B2DAA32-4106-4B4A-8616-8C3A7D6C7230}
AppName=LoL Account Switcher
AppVersion=2.0.0
AppPublisher=LoL Account Switcher Contributors
DefaultDirName={localappdata}\Programs\LoLSwitcher
DefaultGroupName=LoL Account Switcher
DisableProgramGroupPage=yes

; Per-user install — no UAC/admin elevation prompt. The app only ever writes
; to per-user %APPDATA% and the current user's Windows Credential Manager
; vault, so a per-machine (admin) install is unnecessary (D-14).
PrivilegesRequired=lowest

SetupIconFile=assets\icon.ico
UninstallDisplayIcon={app}\LoLSwitcher.exe

OutputDir=.
OutputBaseFilename=LoLSwitcher-v2.0.0-Setup

WizardStyle=modern
Compression=lzma2
SolidCompression=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
; Copy the full PyInstaller --onedir output tree verbatim.
Source: "dist\LoLSwitcher\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

; Bundled WebView2 Evergreen Bootstrapper — a build-time input, NOT committed
; to the repo (downloaded fresh in 06-04, Pitfall 4 freshness). This script
; only references it by name; it must be present at the project root
; alongside this .iss when ISCC runs.
Source: "MicrosoftEdgeWebview2Setup.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall

[Icons]
Name: "{group}\LoL Account Switcher"; Filename: "{app}\LoLSwitcher.exe"
Name: "{group}\Uninstall LoL Account Switcher"; Filename: "{uninstallexe}"
Name: "{autodesktop}\LoL Account Switcher"; Filename: "{app}\LoLSwitcher.exe"; Tasks: desktopicon

[Run]
; Best-effort silent WebView2 Runtime install. WebView2 may already be present
; on the target machine (common on Windows 11) — do NOT gate install success
; on this step's exit code. This handles the WebView2 prerequisite
; automatically for installer users (unlike the portable-zip path, which
; requires a manual prerequisite step documented in README.md).
Filename: "{tmp}\MicrosoftEdgeWebview2Setup.exe"; Parameters: "/silent /install"; Flags: waituntilterminated runhidden skipifdoesntexist
