from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
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
    expected_name = "dot.exe" if IS_WIN else "dot"
    candidates = []

    dot = env_path("GRAPHVIZ_DOT")
    candidates.append(dot)
    sibling = dot.with_name(expected_name)
    if sibling.exists():
        candidates.append(sibling)

    resolved = shutil.which(expected_name)
    if resolved:
        candidates.append(Path(resolved))

    if IS_WIN:
        for env_name in ("ProgramFiles", "ProgramFiles(x86)"):
            root = os.environ.get(env_name)
            if root:
                candidates.append(Path(root) / "Graphviz" / "bin" / expected_name)

    seen = set()
    existing = []
    for candidate in candidates:
        key = str(candidate).lower() if IS_WIN else str(candidate)
        if key in seen:
            continue
        seen.add(key)
        if candidate.exists() and (candidate.name.lower() == expected_name or not IS_WIN):
            existing.append(candidate)

    if IS_WIN:
        existing.sort(key=lambda path: "chocolatey" in str(path).lower())

    for candidate in existing:
        if graphviz_dot_works(candidate):
            return candidate

    searched = ", ".join(str(candidate) for candidate in candidates)
    raise RuntimeError(f"could not resolve a working Graphviz executable {expected_name!r}; searched: {searched}")


def graphviz_dot_works(dot: Path) -> bool:
    try:
        proc = subprocess.run([str(dot), "-V"], capture_output=True, text=True, check=False)
    except OSError:
        return False
    output = (proc.stdout + proc.stderr).lower()
    return proc.returncode == 0 and "graphviz" in output


def graphviz_runtime_env(runtime_root: Path) -> dict[str, str]:
    target = runtime_root / "graphviz"
    graphviz_bin = target / "bin"
    graphviz_lib = target / "lib"
    graphviz_plugin_dir = graphviz_lib / "graphviz"
    env = os.environ.copy()
    path_prefixes = [str(path) for path in (graphviz_bin, graphviz_lib) if path.exists()]
    if path_prefixes:
        env["PATH"] = os.pathsep.join(path_prefixes + ([env.get("PATH", "")] if env.get("PATH") else []))
    dot = graphviz_bin / ("dot.exe" if IS_WIN else "dot")
    env["GRAPHVIZ_DOT"] = str(dot)
    env["PLANTUML_GRAPHVIZ_DOT"] = str(dot)
    if graphviz_lib.exists():
        plugin_config_dir = graphviz_plugin_dir if graphviz_plugin_dir.exists() else graphviz_bin
        env["GVBINDIR"] = str(plugin_config_dir)
        if graphviz_plugin_dir.exists():
            env["GV_PLUGIN_PATH"] = str(graphviz_plugin_dir)
            env["GRAPHVIZ_PLUGIN_PATH"] = str(graphviz_plugin_dir)
        if IS_MAC:
            existing = env.get("DYLD_LIBRARY_PATH", "")
            env["DYLD_LIBRARY_PATH"] = str(graphviz_lib) + (os.pathsep + existing if existing else "")
        elif not IS_WIN:
            existing = env.get("LD_LIBRARY_PATH", "")
            env["LD_LIBRARY_PATH"] = str(graphviz_lib) + (os.pathsep + existing if existing else "")
    return env


def validate_graphviz_runtime(runtime_root: Path) -> None:
    dot = runtime_root / "graphviz" / "bin" / ("dot.exe" if IS_WIN else "dot")
    if not dot.exists():
        raise RuntimeError(f"Graphviz executable was not bundled: {dot}")
    with tempfile.TemporaryDirectory(prefix="mdoc_graphviz_smoke_") as td:
        src = Path(td) / "smoke.dot"
        out = Path(td) / "smoke.png"
        src.write_text("digraph G { a -> b }\n", encoding="utf-8")
        proc = subprocess.run(
            [str(dot), "-Tpng", str(src), "-o", str(out)],
            capture_output=True,
            text=True,
            check=False,
            env=graphviz_runtime_env(runtime_root),
        )
        if proc.returncode != 0 or not out.exists():
            message = proc.stderr.strip() or proc.stdout.strip() or "Graphviz runtime smoke test did not produce PNG"
            raise RuntimeError(f"bundled Graphviz runtime failed smoke test: {message}")


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
        "zlib*.dll", "libiconv*.dll", "*.dll",
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

    if IS_WIN:
        root = dot.parent.parent
        _copy_graphviz_libs(dot.parent, bin_dir)
        if (dot.parent / "config6").exists():
            copy_file(dot.parent / "config6", bin_dir / "config6")
        if (root / "lib" / "graphviz").exists():
            copy_tree(root / "lib" / "graphviz", target / "lib" / "graphviz")
        validate_graphviz_runtime(runtime_root)
        return

    if IS_MAC:
        root = dot.parents[1]
        _copy_graphviz_libs(root / "lib", lib_dir)
        if (root / "lib" / "graphviz").exists():
            copy_tree(root / "lib" / "graphviz", target / "lib" / "graphviz")
        validate_graphviz_runtime(runtime_root)
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
    validate_graphviz_runtime(runtime_root)


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
