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
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtWebChannel", "PySide6.QtWebSockets", "PySide6.QtWebView",
    "PySide6.QtQuick3D", "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DExtras",
    "PySide6.Qt3DInput", "PySide6.Qt3DLogic", "PySide6.Qt3DAnimation",
    "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtGraphs",
    "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtDesigner", "PySide6.QtHelp",
    "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtPositioning", "PySide6.QtLocation",
    "PySide6.QtSensors", "PySide6.QtSerialPort", "PySide6.QtSerialBus",
    "PySide6.QtRemoteObjects", "PySide6.QtScxml", "PySide6.QtSql", "PySide6.QtTest",
    "PySide6.QtTextToSpeech", "PySide6.QtSpatialAudio", "PySide6.QtHttpServer",
    "PySide6.QtQuickControls2", "PySide6.QtQuickWidgets",
]

# Files PySide6's hooks bring along that nothing in Haystacks loads (checked
# with the DLLs' import tables): Qt Quick/QML and the on-screen keyboard that
# needs them, PDF image support, the software OpenGL fallback (video is drawn
# with Direct3D) and Qt's own translations. About 55 MB.
DROP = ("opengl32sw.dll", "Qt6Quick", "Qt6Qml", "Qt6VirtualKeyboard", "qtvirtualkeyboard",
        "Qt6Pdf", "qpdf.dll", "PySide6/translations/", "PySide6\\translations\\")

a = Analysis(
    ["Haystacks.pyw"],
    datas=collect_data_files("orukeet") + [("assets/haystacks.ico", "assets")],
    excludes=EXCLUDES,
    noarchive=False,
)
keep = lambda entry: not any(d.lower() in entry[0].lower() for d in DROP)
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
