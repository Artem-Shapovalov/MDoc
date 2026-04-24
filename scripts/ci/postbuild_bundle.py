from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / "dist"
IS_WIN = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"
JAVA_MODULES = [
    "java.base",
    "java.datatransfer",
    "java.desktop",
    "java.logging",
    "java.naming",
    "java.prefs",
    "java.xml",
]


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
    if IS_WIN and len(value) >= 3 and value[0] == "/" and value[2] == "/":
        value = f"{value[1].upper()}:{value[2:]}"
    path = Path(value)
    if not path.exists():
        raise RuntimeError(f"path from {name} does not exist: {path}")
    return path


def resolve_graphviz_dot() -> Path:
    dot = env_path("GRAPHVIZ_DOT").resolve()
    expected_name = "dot.exe" if IS_WIN else "dot"
    if dot.name.lower() == expected_name:
        return dot

    sibling = dot.with_name(expected_name)
    if sibling.exists():
        return sibling.resolve()

    resolved = shutil.which(expected_name)
    if resolved:
        return Path(resolved).resolve()

    if not IS_WIN and dot.name == expected_name:
        return dot

    raise RuntimeError(f"could not resolve real Graphviz executable {expected_name!r} from {dot}")


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


def _copy_graphviz_libs(src_dir: Path, dst_dir: Path) -> None:
    patterns = [
        "libgvc*", "libcgraph*", "libcdt*", "libpathplan*", "libxdot*", "libexpat*",
        "gvc*.dll", "cgraph*.dll", "cdt*.dll", "pathplan*.dll", "xdot*.dll", "expat*.dll",
        "zlib*.dll", "libiconv*.dll",
    ]
    for pattern in patterns:
        for src in src_dir.glob(pattern):
            if src.is_file():
                copy_file(src, dst_dir / src.name)


def copy_graphviz(runtime_root: Path) -> None:
    dot = resolve_graphviz_dot()
    target = runtime_root / "graphviz"
    bin_dir = ensure_dir(target / "bin")
    lib_dir = ensure_dir(target / "lib")

    # Keep a canonical executable name in the runtime payload. Some Windows
    # shells resolve `dot` to a wrapper, while the application and PlantUML need
    # a stable bundled executable path at runtime.
    bundled_dot_name = "dot.exe" if IS_WIN else "dot"
    copy_file(dot, bin_dir / bundled_dot_name)
    if not (bin_dir / bundled_dot_name).exists():
        raise RuntimeError(f"Graphviz executable was not bundled: {bin_dir / bundled_dot_name}")

    if IS_WIN:
        root = dot.parent.parent
        _copy_graphviz_libs(dot.parent, bin_dir)
        if (dot.parent / "config6").exists():
            copy_file(dot.parent / "config6", bin_dir / "config6")
        if (root / "lib" / "graphviz").exists():
            copy_tree(root / "lib" / "graphviz", target / "lib" / "graphviz")
        return

    if IS_MAC:
        root = dot.parents[1]
        _copy_graphviz_libs(root / "lib", lib_dir)
        if (root / "lib" / "graphviz").exists():
            copy_tree(root / "lib" / "graphviz", target / "lib" / "graphviz")
        return

    _copy_graphviz_libs(Path("/usr/lib"), lib_dir)
    for lib_dir_path in (
        Path("/usr/lib"),
        Path("/usr/lib64"),
        Path("/lib/x86_64-linux-gnu"),
        Path("/lib/aarch64-linux-gnu"),
        Path("/usr/lib/x86_64-linux-gnu"),
        Path("/usr/lib/aarch64-linux-gnu"),
    ):
        if lib_dir_path.exists():
            _copy_graphviz_libs(lib_dir_path, lib_dir)
    for plugin_dir in (
        Path("/usr/lib/graphviz"),
        Path("/usr/lib64/graphviz"),
        Path("/usr/lib/x86_64-linux-gnu/graphviz"),
        Path("/usr/lib/aarch64-linux-gnu/graphviz"),
    ):
        if plugin_dir.exists():
            copy_tree(plugin_dir, target / "lib" / "graphviz")
            break


def copy_java(runtime_root: Path) -> None:
    java_bin = env_path("JAVA_BIN").resolve()
    java_home = java_bin.parent.parent if IS_WIN else java_bin.parents[1]
    jlink = java_home / "bin" / ("jlink.exe" if IS_WIN else "jlink")
    target = runtime_root / "java"
    if target.exists():
        shutil.rmtree(target)
    if jlink.exists():
        cmd = [
            str(jlink),
            "--add-modules", ",".join(JAVA_MODULES),
            "--strip-debug",
            "--no-header-files",
            "--no-man-pages",
            "--compress=2",
            "--output", str(target),
        ]
        subprocess.run(cmd, check=True)
        return
    copy_tree(java_home, target)


def main() -> None:
    runtime_root = ensure_dir(bundle_root())
    copy_plantuml(runtime_root)
    copy_fonts(runtime_root)
    copy_graphviz(runtime_root)
    copy_java(runtime_root)
    print(f"Prepared bundled runtime at {runtime_root}")


if __name__ == "__main__":
    main()
