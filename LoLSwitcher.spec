# LoLSwitcher.spec — PyInstaller build spec for LoL Account Switcher
#
# Build command (from project root):
#   pyinstaller LoLSwitcher.spec
#
# NOTE: --onefile is FORBIDDEN (COMMON-3 in RESEARCH.md).
#   --onefile extracts to a temp directory on every launch and triggers
#   Windows Defender / AV heuristics.  Always use --onedir (the default
#   for Analysis builds).
#
# keyring hiddenimports: RESEARCH.md Pitfall 1 documents that the old
#   keyring Windows backend module was removed in keyring 25.x and replaced
#   by win32ctypes.  The correct imports for keyring 25.7.0 are listed below.
#
# Python.Runtime.dll fallback (RESEARCH.md Topic 9 rough edge):
#   If the frozen exe crashes with a CLR / Python.Runtime.dll error, add the
#   following entry to the datas list and rebuild:
#     (os.path.join(sys.prefix, 'Lib', 'site-packages', 'pythonnet', 'Python.Runtime.dll'), '.'),
#   This bundles the managed runtime bridge alongside the executable.

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=[
        ('gui/assets', 'gui/assets'),  # Bundles index.html + app.js + fonts/ + js/Sortable.min.js
        ('assets/icon.ico', 'assets'),  # D-15: runtime file for the taskbar window icon (sys._MEIPASS fallback)
    ],
    hiddenimports=[
        # pywebview / WebView2 backend (Windows)
        "webview",
        "webview.platforms.edgechromium",
        "clr",
        "clr_loader",
        # keyring 25.x Windows Credential Manager backend
        "keyring.backends.Windows",           # Windows Credential Manager backend
        "win32ctypes.pywin32.pywintypes",     # Required by keyring 25.x on Windows
        "win32ctypes.pywin32.win32cred",      # Required by keyring 25.x on Windows
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Non-Windows webview backends (not needed on Windows)
        "webview.platforms.gtk",
        "webview.platforms.cocoa",
        "webview.platforms.cef",
        "gi",
        # Qt bindings (not used)
        "PyQt5",
        "PyQt6",
        "PySide2",
        "PySide6",
        # customtkinter removed in v2.0
        "customtkinter",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="LoLSwitcher",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,      # --noupx: UPX compression triggers AV false positives
    console=False,  # --windowed: no console window shown to user
    icon="assets/icon.ico",  # Requires assets/icon.ico — create before packaging
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="LoLSwitcher",
)
