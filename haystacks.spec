# PyInstaller recipe for Haystacks: a folder (onedir) build with no console.
# Built by build.ps1 locally and by .github/workflows/release.yml for releases:
#     python -m PyInstaller --noconfirm --clean haystacks.spec
# Onedir (not onefile) starts faster, trips antivirus less, and keeps the Qt
# DLLs as separate, replaceable files (LGPL).
from PyInstaller.utils.hooks import collect_data_files

# Qt and Python parts Haystacks never uses; leaving them out keeps the
# installer smaller.
EXCLUDES = [
    "tkinter", "unittest", "pydoc_data",
    "PySide6.QtWebEngineQuick", "PySide6.QtWebSockets", "PySide6.QtWebView",
    "PySide6.QtQuick3D", "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DExtras",
    "PySide6.Qt3DInput", "PySide6.Qt3DLogic", "PySide6.Qt3DAnimation",
    "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtGraphs",
    "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtDesigner", "PySide6.QtHelp",
    "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtLocation",
    "PySide6.QtSensors", "PySide6.QtSerialPort", "PySide6.QtSerialBus",
    "PySide6.QtRemoteObjects", "PySide6.QtScxml", "PySide6.QtSql", "PySide6.QtTest",
    "PySide6.QtTextToSpeech", "PySide6.QtSpatialAudio", "PySide6.QtHttpServer",
    "PySide6.QtQuickControls2", "PySide6.QtQuickWidgets", "PySide6.QtPositioning",
    # The web engine loads Qt Quick/QML's DLLs itself; leaving out their Python
    # modules keeps PyInstaller from bringing every QML plugin along (~100 MB).
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtOpenGL",
]

# Files PySide6's hooks bring along that nothing in Haystacks loads (checked
# with the DLLs' import tables): the on-screen keyboard, PDF image support, the
# software OpenGL fallback (video is drawn with Direct3D), Qt's own
# translations and the web engine's debugging files. Qt Quick/QML stay: the
# web engine that plays YouTube videos needs them.
DROP = ("opengl32sw.dll", "Qt6VirtualKeyboard", "qtvirtualkeyboard", "Qt6Pdf", "qpdf.dll",
        "PySide6/translations/", "PySide6\\translations\\", ".debug.pak", ".debug.bin",
        "qtwebengine_devtools_resources", "qmltooling", "libcrypto-3-x64.dll",
        "libssl-3-x64.dll")
# The web engine needs its English strings; its other languages are dropped.
KEEP = ("qtwebengine_locales/en-US.pak", "qtwebengine_locales\\en-US.pak")

a = Analysis(
    ["Haystacks.pyw"],
    datas=collect_data_files("orukeet") + [("assets/haystacks.ico", "assets")],
    excludes=EXCLUDES,
    noarchive=False,
)
keep = lambda entry: (any(k.lower() in entry[0].lower() for k in KEEP)
                      or not any(d.lower() in entry[0].lower() for d in DROP))
a.binaries = [b for b in a.binaries if keep(b)]
a.datas = [d for d in a.datas if keep(d)]
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Haystacks",
    icon="assets/haystacks.ico",
    console=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Haystacks", upx=False)
