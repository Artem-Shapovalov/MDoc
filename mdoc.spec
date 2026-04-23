# -*- mode: python ; coding: utf-8 -*-

import sys
from pathlib import Path

project_root = Path.cwd()
main_script = project_root / "mdoc.py"
is_windows = sys.platform.startswith("win")

QT_EXCLUDES = [
    "PySide6.Qt3DAnimation", "PySide6.Qt3DCore", "PySide6.Qt3DExtras", "PySide6.Qt3DInput", "PySide6.Qt3DLogic",
    "PySide6.Qt3DRender", "PySide6.QtBluetooth", "PySide6.QtCharts", "PySide6.QtConcurrent",
    "PySide6.QtDataVisualization", "PySide6.QtDesigner", "PySide6.QtGraphs", "PySide6.QtHelp",
    "PySide6.QtHttpServer", "PySide6.QtLocation", "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets",
    "PySide6.QtNetworkAuth", "PySide6.QtNfc", "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets",
    "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtPositioning", "PySide6.QtPrintSupport",
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.QtQuickControls2",
    "PySide6.QtQuickTest", "PySide6.QtQuickWidgets", "PySide6.QtRemoteObjects", "PySide6.QtScxml",
    "PySide6.QtSensors", "PySide6.QtSerialBus", "PySide6.QtSerialPort", "PySide6.QtSpatialAudio",
    "PySide6.QtSql", "PySide6.QtStateMachine", "PySide6.QtSvg", "PySide6.QtSvgWidgets", "PySide6.QtTest",
    "PySide6.QtTextToSpeech", "PySide6.QtUiTools", "PySide6.QtWebChannel", "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineQuick", "PySide6.QtWebEngineWidgets", "PySide6.QtWebSockets", "PySide6.QtWebView",
    "PySide6.QtXml", "PySide6.QtXmlPatterns",
]
MATPLOTLIB_EXCLUDES = [
    "matplotlib.backends.backend_qt", "matplotlib.backends.backend_qt5", "matplotlib.backends.backend_qtagg",
    "matplotlib.backends.backend_qt5agg", "matplotlib.backends.backend_tkagg", "matplotlib.backends.backend_tkcairo",
    "matplotlib.backends.backend_wx", "matplotlib.backends.backend_wxagg", "matplotlib.backends.backend_webagg",
]

datas = []

for resource_dir in ("assets", "fonts", "resources", "templates", "static"):
    candidate = project_root / resource_dir
    if candidate.exists():
        datas.append((str(candidate), resource_dir))

plantuml_jar = project_root / "third_party" / "plantuml" / "plantuml.jar"
if plantuml_jar.exists():
    datas.append((str(plantuml_jar), "third_party/plantuml"))

a = Analysis(
    [str(main_script)],
    pathex=[str(project_root)],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=QT_EXCLUDES + MATPLOTLIB_EXCLUDES,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MDoc",
    debug=False,
    bootloader_ignore_signals=False,
    strip=not is_windows,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=not is_windows,
    upx=False,
    upx_exclude=[],
    name="MDoc",
)

app = BUNDLE(
    coll,
    name="MDoc.app",
    icon=None,
    bundle_identifier="com.example.mdoc",
)
