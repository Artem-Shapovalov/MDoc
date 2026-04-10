# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

project_root = Path.cwd()
main_script = project_root / "mdoc.py"

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
    excludes=[],
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
    strip=False,
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
    strip=False,
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
