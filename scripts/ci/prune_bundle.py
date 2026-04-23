from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / "dist"
IS_MAC = sys.platform == "darwin"

UNUSED_PYSIDE = [
    "Qt3DAnimation", "Qt3DCore", "Qt3DExtras", "Qt3DInput", "Qt3DLogic", "Qt3DRender",
    "QtBluetooth", "QtCharts", "QtConcurrent", "QtDataVisualization", "QtDesigner",
    "QtGraphs", "QtHelp", "QtHttpServer", "QtLocation", "QtMultimedia", "QtMultimediaWidgets",
    "QtNetworkAuth", "QtNfc", "QtOpenGL", "QtOpenGLWidgets", "QtPdf", "QtPdfWidgets",
    "QtPositioning", "QtPrintSupport", "QtQml", "QtQuick", "QtQuick3D", "QtQuickControls2",
    "QtQuickTest", "QtQuickWidgets", "QtRemoteObjects", "QtScxml", "QtSensors", "QtSerialBus",
    "QtSerialPort", "QtSpatialAudio", "QtSql", "QtStateMachine", "QtSvg", "QtSvgWidgets",
    "QtTest", "QtTextToSpeech", "QtUiTools", "QtWebChannel", "QtWebEngineCore",
    "QtWebEngineQuick", "QtWebEngineWidgets", "QtWebSockets", "QtWebView", "QtXml", "QtXmlPatterns",
]
UNUSED_PLUGIN_DIRS = [
    "assetimporters", "audio", "canbus", "designer", "gamepads", "geoservices", "geometryloaders",
    "iconengines", "imageformats", "multimedia", "networkinformation", "position", "printsupport",
    "qmltooling", "renderers", "renderplugins", "sceneparsers", "scxmldatamodel", "sensors",
    "sqldrivers", "texttospeech", "tls", "wayland-decoration-client", "wayland-graphics-integration-client",
    "wayland-shell-integration", "webview", "xcbglintegrations",
]
KEEP_IMAGEFORMAT_PLUGINS = {"qgif", "qico", "qjpeg", "qsvg"}


def bundle_root() -> Path:
    if IS_MAC:
        return DIST / "MDoc.app" / "Contents" / "MacOS"
    return DIST / "MDoc"


def remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path, ignore_errors=True)


def prune_plugins(plugins_root: Path) -> None:
    if not plugins_root.exists():
        return
    for name in UNUSED_PLUGIN_DIRS:
        remove_path(plugins_root / name)
    imageformats = plugins_root / "imageformats"
    if imageformats.exists():
        for entry in imageformats.iterdir():
            stem = entry.stem.lower()
            if stem not in KEEP_IMAGEFORMAT_PLUGINS:
                remove_path(entry)


def main() -> None:
    root = bundle_root()
    if not root.exists():
        raise SystemExit(f"bundle root was not found: {root}")
    for name in UNUSED_PYSIDE:
        for path in root.rglob(f"{name}.*"):
            remove_path(path)
    for qml_dir in list(root.rglob("PySide6/qml")) + list(root.rglob("PySide6/Qt/qml")):
        remove_path(qml_dir)
    for tr_dir in list(root.rglob("PySide6/translations")) + list(root.rglob("PySide6/Qt/translations")):
        remove_path(tr_dir)
    for ex_dir in list(root.rglob("PySide6/examples")):
        remove_path(ex_dir)
    for plugins_root in list(root.rglob("PySide6/Qt/plugins")) + list(root.rglob("plugins")):
        prune_plugins(plugins_root)
    print(f"Pruned bundle at {root}")


if __name__ == "__main__":
    main()
