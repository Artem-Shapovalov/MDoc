from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / "dist"
IS_WIN = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"


def bundle_root() -> Path:
    if IS_MAC:
        return DIST / "MDoc.app" / "Contents" / "Resources" / "runtime"
    return DIST / "MDoc" / "runtime"


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def copy_file(src: Path, dst: Path) -> None:
    ensure_dir(dst.parent)
    shutil.copy2(src, dst)


def copy_tree(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)


def env_path(name: str) -> Path:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"required environment variable is missing: {name}")
    path = Path(value)
    if not path.exists():
        raise RuntimeError(f"path from {name} does not exist: {path}")
    return path


def copy_plantuml(runtime_root: Path) -> None:
    plantuml = ROOT / "third_party" / "plantuml" / "plantuml.jar"
    if not plantuml.exists():
        raise RuntimeError(f"PlantUML jar was not found: {plantuml}")
    copy_file(plantuml, runtime_root / "plantuml" / "plantuml.jar")


def copy_fonts(runtime_root: Path) -> None:
    fonts_root = ROOT / "third_party" / "fonts"
    if not fonts_root.exists():
        raise RuntimeError(f"Bundled fonts directory was not found: {fonts_root}")
    copy_tree(fonts_root, runtime_root / "fonts")


def copy_graphviz(runtime_root: Path) -> None:
    dot = env_path("GRAPHVIZ_DOT")
    target = runtime_root / "graphviz"

    if IS_WIN or IS_MAC:
        copy_tree(dot.parent.parent, target)
        return

    bin_dir = ensure_dir(target / "bin")
    lib_dir = ensure_dir(target / "lib")
    copy_file(dot, bin_dir / "dot")

    plugin_dirs = [
        Path("/usr/lib/graphviz"),
        Path("/usr/lib64/graphviz"),
        Path("/usr/lib/x86_64-linux-gnu/graphviz"),
        Path("/usr/lib/aarch64-linux-gnu/graphviz"),
    ]
    lib_dirs = [
        Path("/usr/lib"),
        Path("/usr/lib64"),
        Path("/lib/x86_64-linux-gnu"),
        Path("/lib/aarch64-linux-gnu"),
        Path("/usr/lib/x86_64-linux-gnu"),
        Path("/usr/lib/aarch64-linux-gnu"),
    ]

    for plugin_dir in plugin_dirs:
        if plugin_dir.exists():
            copy_tree(plugin_dir, target / "lib" / "graphviz")
            break

    for lib_dir_path in lib_dirs:
        if not lib_dir_path.exists():
            continue
        for pattern in ("libgvc*", "libcgraph*", "libcdt*", "libpathplan*", "libxdot*", "libexpat*"):
            for src in lib_dir_path.glob(pattern):
                if src.is_file():
                    copy_file(src, lib_dir / src.name)


def copy_java(runtime_root: Path) -> None:
    java_bin = env_path("JAVA_BIN")
    if IS_WIN:
        java_home = java_bin.parent.parent
    elif IS_MAC:
        java_home = java_bin.resolve().parents[2]
    else:
        java_home = java_bin.resolve().parents[1]
    copy_tree(java_home, runtime_root / "java")


def main() -> None:
    runtime_root = ensure_dir(bundle_root())
    copy_plantuml(runtime_root)
    copy_fonts(runtime_root)
    copy_graphviz(runtime_root)
    copy_java(runtime_root)
    print(f"Prepared bundled runtime at {runtime_root}")


if __name__ == "__main__":
    main()
