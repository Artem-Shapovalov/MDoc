#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import os
import re
import shlex
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFont, ImageOps
from pygments import lex
from pygments.lexers import CLexer, CppLexer, JavaLexer, PythonLexer, TexLexer
from pygments.token import Token
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

try:
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib.mathtext import math_to_image
    MATPLOTLIB_AVAILABLE = True
except Exception:
    MATPLOTLIB_AVAILABLE = False

try:
    import cairosvg
    CAIROSVG_AVAILABLE = True
except Exception:
    CAIROSVG_AVAILABLE = False

try:
    from PySide6.QtCore import QRect, QSize, Qt, QTimer, Signal, QObject, QRunnable, QThreadPool, QStringListModel
    from PySide6.QtGui import (
        QAction,
        QColor,
        QFont,
        QImage,
        QKeySequence,
        QPainter,
        QPixmap,
        QTextCharFormat,
        QTextCursor,
        QTextOption,
        QSyntaxHighlighter,
    )
    from PySide6.QtWidgets import (
        QApplication,
        QCompleter,
        QFileDialog,
        QHBoxLayout,
        QLabel,
        QMainWindow,
        QMessageBox,
        QPlainTextEdit,
        QScrollArea,
        QSplitter,
        QToolBar,
        QVBoxLayout,
        QWidget,
    )
    PYSIDE_AVAILABLE = True
except Exception:
    PYSIDE_AVAILABLE = False

APP_NAME = "MDoc"
PAGE_WIDTH_PT, PAGE_HEIGHT_PT = A4
PAGE_MARGIN_PT = 18 * 72.0 / 25.4
CONTENT_WIDTH_PT = PAGE_WIDTH_PT - PAGE_MARGIN_PT * 2
CONTENT_HEIGHT_PT = PAGE_HEIGHT_PT - PAGE_MARGIN_PT * 2
PREVIEW_DPI = 120
PREVIEW_PAGE_WIDTH_PX = int(PAGE_WIDTH_PT / 72.0 * PREVIEW_DPI)
PREVIEW_PAGE_HEIGHT_PX = int(PAGE_HEIGHT_PT / 72.0 * PREVIEW_DPI)
PREVIEW_MARGIN_PX = int(PAGE_MARGIN_PT / 72.0 * PREVIEW_DPI)
PAGE_GAP_PX = 10
PAGE_BORDER_PX = 1
PAGE_SHADOW_PX = 6
PREVIEW_ZOOM_MIN = 0.25
PREVIEW_ZOOM_MAX = 4.00
PREVIEW_ZOOM_STEP = 1.12
EDITOR_SYNC_GUARD_MS = 120


def app_root_dir() -> Path:
    if getattr(sys, "frozen", False):
        exe_path = Path(sys.executable).resolve()
        if sys.platform == "darwin" and exe_path.parent.name == "MacOS":
            return exe_path.parent.parent.parent
        return exe_path.parent
    return Path(__file__).resolve().parent


def runtime_dir() -> Optional[Path]:
    root = app_root_dir()
    candidates = []
    if sys.platform == "darwin":
        candidates.append(root / "Contents" / "Resources" / "runtime")
    candidates.append(root / "runtime")
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def prepend_env_path(name: str, path: Path) -> None:
    current = os.environ.get(name, "")
    parts = [str(path)]
    if current:
        parts.append(current)
    os.environ[name] = os.pathsep.join(parts)


def bundled_plantuml_cmd() -> Optional[str]:
    rt = runtime_dir()
    if not rt:
        return None
    plantuml_jar = rt / "plantuml" / "plantuml.jar"
    if not plantuml_jar.exists():
        return None
    java_bin = rt / "java" / "bin" / ("java.exe" if os.name == "nt" else "java")
    if java_bin.exists():
        return f'"{java_bin}" -jar "{plantuml_jar}"'
    return f'java -jar "{plantuml_jar}"'


def bootstrap_runtime_environment() -> None:
    rt = runtime_dir()
    if not rt:
        return
    graphviz_root = rt / "graphviz"
    graphviz_bin = graphviz_root / "bin"
    graphviz_lib = graphviz_root / "lib"
    if graphviz_bin.exists():
        prepend_env_path("PATH", graphviz_bin)
        dot_name = "dot.exe" if os.name == "nt" else "dot"
        dot_path = graphviz_bin / dot_name
        if dot_path.exists():
            os.environ.setdefault("GRAPHVIZ_DOT", str(dot_path))
    if graphviz_lib.exists():
        prepend_env_path("PATH", graphviz_lib)
        os.environ.setdefault("GVBINDIR", str(graphviz_bin))
        for name in ("GV_PLUGIN_PATH", "GRAPHVIZ_PLUGIN_PATH"):
            os.environ.setdefault(name, str(graphviz_lib / "graphviz"))
        if sys.platform == "darwin":
            existing = os.environ.get("DYLD_LIBRARY_PATH", "")
            os.environ["DYLD_LIBRARY_PATH"] = str(graphviz_lib) + (os.pathsep + existing if existing else "")
        elif os.name != "nt":
            existing = os.environ.get("LD_LIBRARY_PATH", "")
            os.environ["LD_LIBRARY_PATH"] = str(graphviz_lib) + (os.pathsep + existing if existing else "")
    java_bin_dir = rt / "java" / "bin"
    if java_bin_dir.exists():
        prepend_env_path("PATH", java_bin_dir)


bootstrap_runtime_environment()

TITLE_LINE_RE = re.compile(r"^\s*#\s+Title:\s*(.+?)\s*$")
TOC_LINE_RE = re.compile(r"^\s*#\s+TOC\s*$")
PAGEBREAK_RE = re.compile(r"^\s*<!--\s*pagebreak\s*-->\s*$")
FENCE_RE = re.compile(r"^([`~]{3,})\s*([A-Za-z0-9_+#.-]*)\s*$")
TABLE_SEP_RE = re.compile(r"^\s*\|?(?:\s*:?-+:?\s*\|)+\s*$")
LIST_ITEM_RE = re.compile(r"^(\s*)([-+*]|\d+\.|[A-Za-z]\.)\s+(.*)$")


def list_indent_to_level(indent: str) -> int:
    """Convert whitespace before a list marker into a nesting level.

    MDoc should be forgiving here: people commonly indent nested list items
    with 2, 3, or 4 spaces in lightweight notes. Tabs are expanded first,
    then every 2 columns count as one nesting step.
    """
    cols = len(indent.expandtabs(4))
    if cols < 2:
        return 0
    return cols // 2

IMAGE_ONLY_RE = re.compile(r"^\s*!\[([^\]]*)\]\(([^)]+)\)\s*$")
QUOTE_PREFIX_RE = re.compile(r"^\s*(>\s*)+")
INLINE_TOKEN_RE = re.compile(r"(!?\[[^\]]+\]\([^)]+\)|\*\*\*[^*]+\*\*\*|\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`)")


def pt_to_px(v: float) -> int:
    return max(1, int(round(v / 72.0 * PREVIEW_DPI)))


def html_unescape_minimal(text: str) -> str:
    return text.replace("&lt;", "<").replace("&gt;", ">" ).replace("&amp;", "&")


@dataclass
class Block:
    kind: str
    start_line: int
    end_line: int
    text: str = ""
    meta: dict = field(default_factory=dict)


@dataclass
class Style:
    font_name: str
    font_size: float
    leading: float
    space_before: float = 0.0
    space_after: float = 0.0
    align: str = "left"
    color: Tuple[int, int, int] = (0, 0, 0)
    bold: bool = False
    background: Optional[Tuple[int, int, int]] = None


@dataclass
class InlineRun:
    text: str
    bold: bool = False
    italic: bool = False
    code: bool = False
    link: Optional[str] = None


@dataclass
class RichTextElement:
    x: float
    y: float
    width: float
    lines: List[List[InlineRun]]
    style: Style
    line_indents: List[float] = field(default_factory=list)


@dataclass
class ListItemData:
    level: int
    marker: str
    ordered: bool
    text: str
    start_line: int


@dataclass
class TextElement:
    x: float
    y: float
    width: float
    lines: List[str]
    style: Style


@dataclass
class ImageElement:
    x: float
    y: float
    width: float
    height: float
    image_path: str


@dataclass
class QuoteBoxElement:
    x: float
    y: float
    width: float
    height: float
    level: int = 1


@dataclass
class TablePartElement:
    x: float
    y: float
    width: float
    header: List[str]
    rows: List[List[str]]
    col_widths: List[float]
    row_heights: List[float]
    header_height: float
    line_start: int


@dataclass
class TOCEntryElement:
    x: float
    y: float
    width: float
    title_lines: List[str]
    style: Style
    level: int
    page_number: int


PageElement = RichTextElement | TextElement | ImageElement | QuoteBoxElement | TablePartElement | TOCEntryElement


@dataclass
class PageLayout:
    elements: List[PageElement] = field(default_factory=list)
    content_page_number: Optional[int] = None
    section: str = "content"  # title, toc, content
    first_source_line: Optional[int] = None


@dataclass
class LayoutResult:
    pages: List[PageLayout]
    line_to_page: Dict[int, int]
    heading_page_numbers: Dict[Tuple[int, str], int]
    warnings: List[str] = field(default_factory=list)


@dataclass
class RenderResult:
    page_pngs: List[bytes]
    pdf_bytes: bytes
    line_to_page: Dict[int, int]
    warnings: List[str]


class RenderError(RuntimeError):
    pass


class FontRegistry:
    def __init__(self) -> None:
        self.sans_path = self._find_font_candidates([
            "DejaVuSans.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/TTF/DejaVuSans.ttf",
            "/usr/share/fonts/dejavu/DejaVuSans.ttf",
        ])
        self.sans_bold_path = self._find_font_candidates([
            "DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
        ])
        self.sans_italic_path = self._find_font_candidates([
            "DejaVuSans-Oblique.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf",
            "/usr/share/fonts/TTF/DejaVuSans-Oblique.ttf",
            "/usr/share/fonts/dejavu/DejaVuSans-Oblique.ttf",
        ])
        self.sans_bold_italic_path = self._find_font_candidates([
            "DejaVuSans-BoldOblique.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-BoldOblique.ttf",
            "/usr/share/fonts/TTF/DejaVuSans-BoldOblique.ttf",
            "/usr/share/fonts/dejavu/DejaVuSans-BoldOblique.ttf",
        ])
        self.mono_path = self._find_font_candidates([
            "DejaVuSansMono.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
            "/usr/share/fonts/TTF/DejaVuSansMono.ttf",
            "/usr/share/fonts/dejavu/DejaVuSansMono.ttf",
        ])
        self.mono_bold_path = self._find_font_candidates([
            "DejaVuSansMono-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
            "/usr/share/fonts/TTF/DejaVuSansMono-Bold.ttf",
            "/usr/share/fonts/dejavu/DejaVuSansMono-Bold.ttf",
        ])
        if not self.sans_path or not self.sans_bold_path or not self.mono_path or not self.mono_bold_path:
            raise RenderError("DejaVu fonts were not found on the system. Install dejavu fonts.")
        self._registered = False
        self._pil_cache: Dict[Tuple[str, int, bool, bool], ImageFont.FreeTypeFont] = {}

    @staticmethod
    def _find_font(candidates: Sequence[str]) -> Optional[str]:
        for path in candidates:
            if os.path.exists(path):
                return path
        return None

    @staticmethod
    def _find_font_candidates(candidates: Sequence[str]) -> Optional[str]:
        rt = runtime_dir()
        expanded: List[str] = []
        if rt:
            fonts_root = rt / "fonts"
            for name in candidates:
                if os.path.isabs(name):
                    expanded.append(name)
                else:
                    expanded.append(str(fonts_root / name))
        expanded.extend(candidates)
        return FontRegistry._find_font(expanded)

    def ensure_reportlab(self) -> None:
        if self._registered:
            return
        pdfmetrics.registerFont(TTFont("DDSans", self.sans_path))
        pdfmetrics.registerFont(TTFont("DDSans-Bold", self.sans_bold_path))
        pdfmetrics.registerFont(TTFont("DDSans-Italic", self.sans_italic_path or self.sans_path))
        pdfmetrics.registerFont(TTFont("DDSans-BoldItalic", self.sans_bold_italic_path or self.sans_bold_path))
        pdfmetrics.registerFont(TTFont("DDMono", self.mono_path))
        pdfmetrics.registerFont(TTFont("DDMono-Bold", self.mono_bold_path))
        self._registered = True

    def pil_font(self, family: str, size_px: int, bold: bool = False, italic: bool = False) -> ImageFont.FreeTypeFont:
        key = (family, size_px, bold, italic)
        if key in self._pil_cache:
            return self._pil_cache[key]
        if family == "mono":
            path = self.mono_bold_path if bold else self.mono_path
        else:
            if bold and italic:
                path = self.sans_bold_italic_path or self.sans_bold_path
            elif bold:
                path = self.sans_bold_path
            elif italic:
                path = self.sans_italic_path or self.sans_path
            else:
                path = self.sans_path
        font = ImageFont.truetype(path, size_px)
        self._pil_cache[key] = font
        return font

    def reportlab_font_name(self, family: str, bold: bool = False, italic: bool = False) -> str:
        if family == "mono":
            return "DDMono-Bold" if bold else "DDMono"
        if bold and italic:
            return "DDSans-BoldItalic"
        if bold:
            return "DDSans-Bold"
        if italic:
            return "DDSans-Italic"
        return "DDSans"


class BlockParser:
    def __init__(self, enable_special_markers: bool = True) -> None:
        self.enable_special_markers = enable_special_markers

    def parse(self, text: str) -> List[Block]:
        lines = text.splitlines()
        blocks: List[Block] = []
        i = 0
        paragraph: List[str] = []
        paragraph_start = 0
        title_seen = False
        toc_seen = False

        def flush_paragraph(end_line: int) -> None:
            nonlocal paragraph, paragraph_start
            if not paragraph:
                return
            txt = "\n".join(paragraph).strip("\n")
            if txt.strip():
                blocks.append(Block("paragraph", paragraph_start, end_line, text=txt))
            paragraph = []

        while i < len(lines):
            line = lines[i]
            stripped = line.strip()

            mt = TITLE_LINE_RE.match(line)
            if self.enable_special_markers and mt and not title_seen:
                flush_paragraph(i)
                title_seen = True
                marker_line = i + 1
                title_lines = [f"# {mt.group(1).strip()}"]
                i += 1
                while i < len(lines):
                    if re.match(r"^#\s+.+$", lines[i]):
                        break
                    title_lines.append(lines[i])
                    i += 1
                blocks.append(Block("titlepage", marker_line, i if i else marker_line, text="\n".join(title_lines).strip("\n")))
                continue

            if self.enable_special_markers and TOC_LINE_RE.match(line) and not toc_seen:
                flush_paragraph(i)
                toc_seen = True
                blocks.append(Block("toc", i + 1, i + 1))
                i += 1
                continue

            if PAGEBREAK_RE.match(line):
                flush_paragraph(i)
                blocks.append(Block("pagebreak", i + 1, i + 1))
                i += 1
                continue

            m_fence = FENCE_RE.match(line)
            if m_fence:
                flush_paragraph(i)
                fence = m_fence.group(1)
                lang = (m_fence.group(2) or "").strip().lower()
                start = i + 1
                i += 1
                code_lines: List[str] = []

                def is_closing_fence(candidate: str) -> bool:
                    stripped_candidate = candidate.strip()
                    return bool(stripped_candidate) and stripped_candidate == fence

                while i < len(lines) and not is_closing_fence(lines[i]):
                    code_lines.append(lines[i])
                    i += 1
                if i < len(lines):
                    end = i + 1
                    i += 1
                else:
                    end = len(lines)
                kind = {"plantuml": "plantuml", "puml": "plantuml", "tex": "tex"}.get(lang, "code")
                blocks.append(Block(kind, start, end, text="\n".join(code_lines), meta={"lang": lang, "fence": fence}))
                continue

            if QUOTE_PREFIX_RE.match(line):
                flush_paragraph(i)
                start = i + 1
                quote_lines = []
                while i < len(lines):
                    curr = lines[i]
                    if not curr.strip():
                        quote_lines.append("")
                        i += 1
                        if i < len(lines) and (QUOTE_PREFIX_RE.match(lines[i]) or not lines[i].strip()):
                            continue
                        break
                    if not QUOTE_PREFIX_RE.match(curr):
                        break
                    rest = re.sub(r"^\s*>\s?", "", curr)
                    quote_lines.append(rest)
                    i += 1
                blocks.append(Block("quote", start, i, text="\n".join(quote_lines)))
                continue

            heading = re.match(r"^(#{1,6})\s+(.*)$", line)
            if heading:
                flush_paragraph(i)
                blocks.append(Block("heading", i + 1, i + 1, text=heading.group(2).strip(), meta={"level": len(heading.group(1))}))
                i += 1
                continue

            if LIST_ITEM_RE.match(line):
                flush_paragraph(i)
                start = i + 1
                items: List[ListItemData] = []
                while i < len(lines):
                    if not lines[i].strip():
                        if i + 1 < len(lines) and LIST_ITEM_RE.match(lines[i + 1]):
                            i += 1
                            continue
                        break
                    m_item = LIST_ITEM_RE.match(lines[i])
                    if m_item:
                        indent, marker, item_text = m_item.groups()
                        level = list_indent_to_level(indent)
                        items.append(ListItemData(level=level, marker=marker, ordered=marker.endswith('.'), text=item_text.strip(), start_line=i + 1))
                        i += 1
                        continue
                    if items:
                        items[-1].text += " " + lines[i].strip()
                        i += 1
                        continue
                    break
                blocks.append(Block("list", start, i, meta={"items": items}))
                continue

            m_img = IMAGE_ONLY_RE.match(line)
            if m_img:
                flush_paragraph(i)
                blocks.append(Block("image", i + 1, i + 1, meta={"alt": m_img.group(1), "src": m_img.group(2).strip()}))
                i += 1
                continue

            if "|" in line and i + 1 < len(lines) and TABLE_SEP_RE.match(lines[i + 1]):
                flush_paragraph(i)
                start = i + 1
                rows = [line, lines[i + 1]]
                i += 2
                while i < len(lines) and "|" in lines[i]:
                    rows.append(lines[i])
                    i += 1
                blocks.append(Block("table", start, i, text="\n".join(rows)))
                continue

            if not stripped:
                flush_paragraph(i)
                i += 1
                continue

            if not paragraph:
                paragraph_start = i + 1
            paragraph.append(line)
            i += 1

        flush_paragraph(len(lines))
        return blocks


class RenderCache:
    def __init__(self) -> None:
        self.dir = Path(tempfile.gettempdir()) / "mdoc_cache"
        self.dir.mkdir(parents=True, exist_ok=True)

    def path_for(self, prefix: str, text: str) -> Path:
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return self.dir / f"{prefix}_{digest}.png"


class AssetRenderer:
    def __init__(self, plantuml_cmd: str = "plantuml", dpi: int = 300, base_dir: Optional[str] = None) -> None:
        self.plantuml_cmd = plantuml_cmd
        self.dpi = dpi
        self.cache = RenderCache()
        self.base_dir = Path(base_dir or os.getcwd())

    def render_plantuml(self, source: str) -> str:
        out = self.cache.path_for("plantuml", source)
        if out.exists():
            return str(out)
        with tempfile.TemporaryDirectory(prefix="md_doc_studio_puml_") as td:
            src = Path(td) / "diagram.puml"
            src.write_text(source, encoding="utf-8")
            cmd = shlex.split(self.plantuml_cmd) + ["-tpng", "-DPLANTUML_LIMIT_SIZE=8192", str(src)]
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
            except FileNotFoundError as exc:
                raise RenderError("PlantUML executable was not found.") from exc
            if proc.returncode != 0:
                raise RenderError(proc.stderr.strip() or proc.stdout.strip() or "PlantUML render failed")
            png = src.with_suffix(".png")
            if not png.exists():
                raise RenderError("PlantUML did not produce a PNG file.")
            Image.open(png).save(out)
        return str(out)

    def render_tex(self, source: str) -> str:
        out = self.cache.path_for("tex", source)
        if out.exists():
            return str(out)
        expr = source.strip()
        if not expr:
            raise RenderError("Empty TeX block.")
        if not MATPLOTLIB_AVAILABLE:
            raise RenderError("matplotlib is required for TeX rendering in this build.")
        if not (expr.startswith("$") and expr.endswith("$")):
            expr = f"${expr}$"
        try:
            math_to_image(expr, str(out), dpi=self.dpi, format="png")
        except Exception as exc:
            raise RenderError(f"TeX render failed: {exc}") from exc
        img = Image.open(out).convert("RGBA")
        ImageOps.expand(img, border=16, fill=(255, 255, 255, 0)).save(out)
        return str(out)

    def _rasterize_svg(self, path: Path) -> str:
        if not CAIROSVG_AVAILABLE:
            raise RenderError("CairoSVG is required for SVG image support in this build.")
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        out = self.cache.path_for("svg", digest)
        if out.exists():
            return str(out)
        try:
            cairosvg.svg2png(bytestring=data, write_to=str(out), dpi=self.dpi)
        except Exception as exc:
            raise RenderError(f"SVG render failed: {exc}") from exc
        return str(out)

    def resolve_image(self, src: str) -> str:
        parsed = urllib.parse.urlparse(src)
        if parsed.scheme in ("http", "https"):
            suffix = Path(parsed.path).suffix.lower() or ".img"
            digest = hashlib.sha256(src.encode("utf-8")).hexdigest()[:16]
            name = f".mdoc_img_{digest}{suffix}"
            out = self.base_dir / name
            try:
                with urllib.request.urlopen(src, timeout=10) as r:
                    data = r.read()
                if not out.exists() or out.read_bytes() != data:
                    out.write_bytes(data)
            except urllib.error.URLError as exc:
                raise RenderError(f"Failed to load image URL: {src}") from exc
            if out.suffix.lower() in (".svg", ".svgz"):
                return self._rasterize_svg(out)
            return str(out)
        path = Path(src)
        if not path.is_absolute():
            path = (self.base_dir / src).resolve()
        if not path.exists():
            raise RenderError(f"Image not found: {src}")
        if path.suffix.lower() in (".svg", ".svgz"):
            return self._rasterize_svg(path)
        return str(path)


class Measure:
    def __init__(self, fonts: FontRegistry) -> None:
        self.fonts = fonts
        self.fonts.ensure_reportlab()

    @staticmethod
    def pt_width(text: str, font_name: str, font_size: float) -> float:
        return pdfmetrics.stringWidth(text, font_name, font_size)

    def wrap_text(self, text: str, style: Style, max_width: float) -> List[str]:
        text = html_unescape_minimal(text)
        words = text.replace("\n", " \n ").split()
        if not words:
            return [""]
        lines: List[str] = []
        current = ""
        for word in words:
            if word == "\\n":
                lines.append(current.rstrip())
                current = ""
                continue
            candidate = word if not current else current + " " + word
            if self.pt_width(candidate, style.font_name, style.font_size) <= max_width:
                current = candidate
                continue
            if current:
                lines.append(current)
                current = word
            else:
                # hard split for very long token
                chunk = ""
                for ch in word:
                    cand = chunk + ch
                    if self.pt_width(cand, style.font_name, style.font_size) <= max_width or not chunk:
                        chunk = cand
                    else:
                        lines.append(chunk)
                        chunk = ch
                current = chunk
        if current:
            lines.append(current)
        return lines or [""]

    def text_height(self, lines: List[str], style: Style) -> float:
        return len(lines) * style.leading

    def parse_inline(self, text: str) -> List[InlineRun]:
        text = html_unescape_minimal(text)
        runs: List[InlineRun] = []
        pos = 0
        for m in INLINE_TOKEN_RE.finditer(text):
            if m.start() > pos:
                runs.append(InlineRun(text[pos:m.start()]))
            tok = m.group(0)
            if tok.startswith('!['):
                mm = re.match(r'!\[([^\]]*)\]\(([^)]+)\)', tok)
                runs.append(InlineRun(mm.group(1) if mm else '[image]'))
            elif tok.startswith('['):
                mm = re.match(r'\[([^\]]+)\]\(([^)]+)\)', tok)
                if mm:
                    runs.append(InlineRun(mm.group(1), link=mm.group(2)))
                else:
                    runs.append(InlineRun(tok))
            elif tok.startswith('***') and tok.endswith('***'):
                runs.append(InlineRun(tok[3:-3], bold=True, italic=True))
            elif tok.startswith('**') and tok.endswith('**'):
                runs.append(InlineRun(tok[2:-2], bold=True))
            elif tok.startswith('*') and tok.endswith('*'):
                runs.append(InlineRun(tok[1:-1], italic=True))
            elif tok.startswith('`') and tok.endswith('`'):
                runs.append(InlineRun(tok[1:-1], code=True))
            else:
                runs.append(InlineRun(tok))
            pos = m.end()
        if pos < len(text):
            runs.append(InlineRun(text[pos:]))
        return runs

    def _font_name_for_run(self, run: InlineRun) -> str:
        family = 'mono' if run.code else 'sans'
        return self.fonts.reportlab_font_name(family, run.bold, run.italic)

    def _tokenize_runs(self, runs: List[InlineRun]) -> List[InlineRun]:
        out: List[InlineRun] = []
        for run in runs:
            parts = re.split(r'(\s+)', run.text)
            for part in parts:
                if part == '':
                    continue
                out.append(InlineRun(part, run.bold, run.italic, run.code, run.link))
        return out

    def _run_width(self, run: InlineRun, style: Style) -> float:
        return pdfmetrics.stringWidth(run.text, self._font_name_for_run(run), style.font_size)

    def wrap_inline(self, runs: List[InlineRun], style: Style, max_width: float) -> List[List[InlineRun]]:
        tokens = self._tokenize_runs(runs)
        lines: List[List[InlineRun]] = []
        current: List[InlineRun] = []
        current_w = 0.0
        for token in tokens:
            is_space = token.text.isspace()
            width = self._run_width(token, style)
            if not current and is_space:
                continue
            if current and current_w + width > max_width and not is_space:
                lines.append(current)
                current = []
                current_w = 0.0
            if not current and is_space:
                continue
            current.append(token)
            current_w += width
        if current or not lines:
            lines.append(current)
        return lines

    def wrap_list_item(self, prefix: str, body_runs: List[InlineRun], style: Style, max_width: float) -> Tuple[List[List[InlineRun]], List[float]]:
        prefix_runs = [InlineRun(prefix)]
        prefix_width = sum(self._run_width(t, style) for t in self._tokenize_runs(prefix_runs))
        body_tokens = self._tokenize_runs(body_runs)
        lines: List[List[InlineRun]] = []
        indents: List[float] = []
        first_line = prefix_runs.copy()
        line_w = prefix_width
        idx = 0
        while idx < len(body_tokens):
            tok = body_tokens[idx]
            is_space = tok.text.isspace()
            width = self._run_width(tok, style)
            if len(first_line) == len(prefix_runs) and is_space:
                idx += 1
                continue
            if line_w + width > max_width and not is_space and len(first_line) > len(prefix_runs):
                break
            first_line.append(tok)
            line_w += width
            idx += 1
        lines.append(first_line)
        indents.append(0.0)
        max_rest = max_width - prefix_width
        while idx < len(body_tokens):
            line: List[InlineRun] = []
            line_w = 0.0
            while idx < len(body_tokens):
                tok = body_tokens[idx]
                is_space = tok.text.isspace()
                width = self._run_width(tok, style)
                if not line and is_space:
                    idx += 1
                    continue
                if line and line_w + width > max_rest and not is_space:
                    break
                line.append(tok)
                line_w += width
                idx += 1
            if not line:
                break
            lines.append(line)
            indents.append(prefix_width)
        return lines, indents


class LayoutEngine:
    def __init__(self, fonts: FontRegistry, assets: AssetRenderer) -> None:
        self.fonts = fonts
        self.assets = assets
        self.measure = Measure(fonts)
        self.styles = {
            "body": Style("DDSans", 11, 15, 0, 6),
            "list": Style("DDSans", 11, 15, 0, 2),
            "code": Style("DDMono", 10, 13, 0, 4),
            "h1": Style("DDSans-Bold", 22, 28, 10, 8, bold=True),
            "h2": Style("DDSans-Bold", 18, 24, 8, 6, bold=True),
            "h3": Style("DDSans-Bold", 16, 21, 8, 5, bold=True),
            "h4": Style("DDSans-Bold", 14, 18, 6, 4, bold=True),
            "h5": Style("DDSans-Bold", 12, 16, 5, 3, bold=True),
            "h6": Style("DDSans-Bold", 11, 15, 4, 2, bold=True),
            "toc1": Style("DDSans", 12, 16, 0, 2),
            "toc2": Style("DDSans", 11, 15, 0, 1),
            "toc3": Style("DDSans", 10.5, 14, 0, 1),
            "title": Style("DDSans-Bold", 28, 34, 0, 0, align="center", bold=True),
            "quote": Style("DDSans", 11, 15, 0, 6),
        }
        self.quote_padding_pt = 10.0
        self.quote_bar_pt = 4.0
        self.quote_gap_pt = 8.0

    def layout(self, blocks: List[Block]) -> LayoutResult:
        preliminary = [b for b in blocks if b.kind != "toc"]
        prelim = self._layout_blocks(preliminary, {})
        headings = prelim.heading_page_numbers
        return self._layout_blocks(blocks, headings)

    def _layout_blocks(self, blocks: List[Block], known_heading_pages: Dict[Tuple[int, str], int]) -> LayoutResult:
        pages: List[PageLayout] = []
        current = PageLayout()
        y = PAGE_MARGIN_PT
        line_to_page: Dict[int, int] = {}
        heading_page_numbers: Dict[Tuple[int, str], int] = {}
        content_page_num = 0
        warnings: List[str] = []

        def start_new_page(section: str = "content") -> None:
            nonlocal current, y, content_page_num
            if current.elements or current.section != "content":
                pages.append(current)
            current = PageLayout(section=section)
            y = PAGE_MARGIN_PT
            if section == "content":
                content_page_num += 1
                current.content_page_number = content_page_num

        def ensure_space(height: float, keep_with_next: bool = False, section: Optional[str] = None) -> None:
            nonlocal y
            sec = section or current.section or "content"
            if current.content_page_number is None and sec == "content":
                current.section = "content"
                start_new_page("content")
                return
            if y + height > PAGE_HEIGHT_PT - PAGE_MARGIN_PT:
                start_new_page(sec)
            elif keep_with_next and y + height + 25 > PAGE_HEIGHT_PT - PAGE_MARGIN_PT:
                start_new_page(sec)

        def current_page_index() -> int:
            return len(pages)

        def mark_lines(block: Block, page_idx: Optional[int] = None) -> None:
            idx = current_page_index() if page_idx is None else page_idx
            target_page = current if idx == len(pages) else pages[idx]
            if target_page.first_source_line is None:
                target_page.first_source_line = block.start_line
            for ln in range(block.start_line, block.end_line + 1):
                line_to_page.setdefault(ln, idx)

        current.section = "content"
        start_new_page("content")

        title_seen = False
        for block in blocks:

            if block.kind == "titlepage":
                if current.elements:
                    pages.append(current)
                title_page = self._layout_title_page(block)
                pages.append(title_page)
                current = PageLayout(section="content")
                y = PAGE_MARGIN_PT
                continue


            if block.kind == "toc":
                toc_items: List[Tuple[int, str, int]] = []
                for candidate in blocks:
                    if candidate.kind == "heading":
                        lvl = candidate.meta.get("level", 1)
                        if lvl <= 3:
                            page_no = known_heading_pages.get((candidate.start_line, candidate.text), 1)
                            toc_items.append((lvl, candidate.text, page_no))
                if current.elements:
                    pages.append(current)
                current = PageLayout(section="toc")
                current.content_page_number = None
                y = PAGE_MARGIN_PT
                title_style = self.styles["h1"]
                current.elements.append(TextElement(PAGE_MARGIN_PT, y, CONTENT_WIDTH_PT, ["Table of contents"], title_style))
                y += title_style.leading + 10
                if current.first_source_line is None:
                    current.first_source_line = block.start_line
                for lvl, text_item, pno in toc_items:
                    style = self.styles[f"toc{min(lvl,3)}"]
                    left_pad = (lvl - 1) * 16
                    max_text_width = CONTENT_WIDTH_PT - left_pad - 36
                    wrapped = self.measure.wrap_text(text_item, style, max_text_width)
                    row_h = self.measure.text_height(wrapped, style)
                    if y + row_h > PAGE_HEIGHT_PT - PAGE_MARGIN_PT:
                        pages.append(current)
                        current = PageLayout(section="toc")
                        current.content_page_number = None
                        y = PAGE_MARGIN_PT
                    current.elements.append(TOCEntryElement(PAGE_MARGIN_PT + left_pad, y, CONTENT_WIDTH_PT - left_pad, wrapped, style, lvl, pno))
                    y += row_h + style.space_after
                pages.append(current)
                current = PageLayout(section="content")
                y = PAGE_MARGIN_PT
                continue


            if block.kind == "pagebreak":
                if current.elements:
                    pages.append(current)
                current = PageLayout(section="content")
                current.content_page_number = None
                y = PAGE_MARGIN_PT
                continue

            if block.kind == "heading":
                style = self.styles[f"h{min(block.meta.get('level', 1),6)}"]
                if block.meta.get("align") == "center":
                    style = replace(style, align="center")
                lines = self.measure.wrap_text(block.text, style, CONTENT_WIDTH_PT)
                h = style.space_before + self.measure.text_height(lines, style) + style.space_after
                ensure_space(h, keep_with_next=True)
                y += style.space_before
                current.elements.append(TextElement(PAGE_MARGIN_PT, y, CONTENT_WIDTH_PT, lines, style))
                current.section = "content"
                if current.first_source_line is None:
                    current.first_source_line = block.start_line
                mark_lines(block)
                heading_page_numbers[(block.start_line, block.text)] = current.content_page_number or 1
                y += self.measure.text_height(lines, style) + style.space_after
                continue

            if block.kind == "paragraph":
                style = self.styles["body"]
                lines = self.measure.wrap_inline(self.measure.parse_inline(block.text), style, CONTENT_WIDTH_PT)
                h = len(lines) * style.leading + style.space_after
                ensure_space(h)
                current.elements.append(RichTextElement(PAGE_MARGIN_PT, y, CONTENT_WIDTH_PT, lines, style, [0.0] * len(lines)))
                if current.first_source_line is None:
                    current.first_source_line = block.start_line
                y += len(lines) * style.leading + style.space_after
                mark_lines(block)
                continue

            if block.kind == "list":
                style = self.styles["list"]
                for item in block.meta.get("items", []):
                    marker = item.marker if item.ordered else "•"
                    base_x = PAGE_MARGIN_PT + item.level * 18.0
                    max_w = max(40.0, CONTENT_WIDTH_PT - item.level * 18.0)
                    lines, indents = self.measure.wrap_list_item(f"{marker} ", self.measure.parse_inline(item.text), style, max_w)
                    h = len(lines) * style.leading + style.space_after
                    ensure_space(h)
                    current.elements.append(RichTextElement(base_x, y, max_w, lines, style, indents))
                    if current.first_source_line is None:
                        current.first_source_line = block.start_line
                    y += len(lines) * style.leading + style.space_after
                mark_lines(block)
                continue

            if block.kind == "quote":
                quote_x = PAGE_MARGIN_PT
                quote_w = CONTENT_WIDTH_PT
                inner_x = quote_x + self.quote_gap_pt + self.quote_bar_pt + self.quote_padding_pt
                inner_w = max(60.0, quote_w - (self.quote_gap_pt + self.quote_bar_pt + self.quote_padding_pt) * 2)
                inner_blocks = BlockParser(enable_special_markers=False).parse(block.text)
                quote_elems, quote_h, quote_warn = self._layout_embedded_blocks(inner_blocks, inner_x, y + self.quote_padding_pt, inner_w)
                outer_h = max(self.quote_padding_pt * 2 + quote_h, 28.0)
                ensure_space(outer_h + 4)
                quote_elems, quote_h, quote_warn = self._layout_embedded_blocks(inner_blocks, inner_x, y + self.quote_padding_pt, inner_w)
                current.elements.append(QuoteBoxElement(quote_x, y, quote_w, outer_h, 1))
                current.elements.extend(quote_elems)
                warnings.extend(quote_warn)
                if current.first_source_line is None:
                    current.first_source_line = block.start_line
                mark_lines(block)
                y += outer_h + 4
                continue

            if block.kind == "image":
                try:
                    path = self.assets.resolve_image(block.meta["src"])
                    with Image.open(path) as img:
                        iw, ih = img.size
                    max_w_px = pt_to_px(CONTENT_WIDTH_PT)
                    max_h_px = pt_to_px(CONTENT_HEIGHT_PT * 0.7)
                    scale = min(max_w_px / iw, max_h_px / ih, 1.0)
                    w_pt = iw * scale / PREVIEW_DPI * 72.0
                    h_pt = ih * scale / PREVIEW_DPI * 72.0
                    ensure_space(h_pt + 8)
                    x = PAGE_MARGIN_PT + (CONTENT_WIDTH_PT - w_pt) / 2
                    current.elements.append(ImageElement(x, y, w_pt, h_pt, path))
                    if current.first_source_line is None:
                        current.first_source_line = block.start_line
                    y += h_pt + 8
                    mark_lines(block)
                except Exception as exc:
                    style = self.styles["code"]
                    lines = self.measure.wrap_text(f"[image render error: {exc}]", style, CONTENT_WIDTH_PT)
                    current.elements.append(TextElement(PAGE_MARGIN_PT, y, CONTENT_WIDTH_PT, lines, style))
                    y += self.measure.text_height(lines, style) + 6
                    warnings.append(str(exc))
                continue

            if block.kind in {"code"}:
                style = self.styles["code"]
                lines = block.text.splitlines() or [""]
                padded_lines = [line if line else " " for line in lines]
                total_h = 8 + len(padded_lines) * style.leading + 8
                if total_h > CONTENT_HEIGHT_PT:
                    warnings.append(f"Code block near line {block.start_line} is taller than one page and was truncated in preview/export.")
                ensure_space(min(total_h, CONTENT_HEIGHT_PT))
                current.elements.append(TextElement(PAGE_MARGIN_PT + 8, y + 8, CONTENT_WIDTH_PT - 16, padded_lines, style))
                if current.first_source_line is None:
                    current.first_source_line = block.start_line
                y += total_h + 4
                mark_lines(block)
                continue

            if block.kind in {"plantuml", "tex"}:
                try:
                    path = self.assets.render_plantuml(block.text) if block.kind == "plantuml" else self.assets.render_tex(block.text)
                    with Image.open(path) as img:
                        iw, ih = img.size
                    max_w_px = pt_to_px(CONTENT_WIDTH_PT)
                    max_h_px = pt_to_px(CONTENT_HEIGHT_PT * 0.7)
                    natural_scale = 2.0 if block.kind == "plantuml" else 1.35
                    scale = min(natural_scale, max_w_px / iw, max_h_px / ih)
                    w_pt = iw * scale / self.assets.dpi * 72.0
                    h_pt = ih * scale / self.assets.dpi * 72.0
                    ensure_space(h_pt + 8)
                    x = PAGE_MARGIN_PT + (CONTENT_WIDTH_PT - w_pt) / 2
                    current.elements.append(ImageElement(x, y, w_pt, h_pt, path))
                    if current.first_source_line is None:
                        current.first_source_line = block.start_line
                    y += h_pt + 8
                    mark_lines(block)
                except Exception as exc:
                    style = self.styles["code"]
                    msg = f"[{block.kind} render error: {exc}]"
                    lines = self.measure.wrap_text(msg, style, CONTENT_WIDTH_PT)
                    current.elements.append(TextElement(PAGE_MARGIN_PT, y, CONTENT_WIDTH_PT, lines, style))
                    y += self.measure.text_height(lines, style) + 6
                    warnings.append(str(exc))
                continue

            if block.kind == "table":
                header, rows = self._parse_table(block.text)
                col_count = max(len(header), max((len(r) for r in rows), default=0))
                header += [""] * (col_count - len(header))
                rows = [r + [""] * (col_count - len(r)) for r in rows]
                if not col_count:
                    continue
                col_widths = self._compute_table_widths(header, rows, col_count)
                header_wrapped, header_h = self._table_row_layout(header, col_widths, True)
                body_layout = [self._table_row_layout(r, col_widths, False) for r in rows]
                keep_together = len(rows) < 5
                total_h = header_h + sum(h for _, h in body_layout)
                if keep_together and y + total_h > PAGE_HEIGHT_PT - PAGE_MARGIN_PT:
                    pages.append(current)
                    current = PageLayout(section="content")
                    start_new_page("content")
                part_rows: List[List[str]] = []
                part_heights: List[float] = []
                start_line = block.start_line
                table_width = min(sum(col_widths), CONTENT_WIDTH_PT)
                table_x = PAGE_MARGIN_PT + max(0.0, (CONTENT_WIDTH_PT - table_width) / 2.0)
                for wrapped_row, row_h in body_layout:
                    if y + header_h + sum(part_heights) + row_h > PAGE_HEIGHT_PT - PAGE_MARGIN_PT:
                        if part_rows:
                            current.elements.append(TablePartElement(table_x, y, table_width, header, part_rows, col_widths, part_heights, header_h, start_line))
                            y += header_h + sum(part_heights) + 6
                            pages.append(current)
                            current = PageLayout(section="content")
                            start_new_page("content")
                            part_rows = []
                            part_heights = []
                    part_rows.append(["\n".join(c) for c in wrapped_row])
                    part_heights.append(row_h)
                if part_rows:
                    current.elements.append(TablePartElement(table_x, y, table_width, header, part_rows, col_widths, part_heights, header_h, start_line))
                    if current.first_source_line is None:
                        current.first_source_line = block.start_line
                    y += header_h + sum(part_heights) + 8
                mark_lines(block)
                continue

        if current.elements or current.section != "content":
            pages.append(current)

        # normalize empty leading content page after title/toc.
        pages = [p for p in pages if p.elements or p.section in {"title", "toc"}]
        content_counter = 0
        index_map = {}
        for idx, page in enumerate(pages):
            index_map[idx] = idx
            if page.section == "content":
                content_counter += 1
                page.content_page_number = content_counter
            else:
                page.content_page_number = None

        normalized_heading_pages: Dict[Tuple[int, str], int] = {}
        for block in blocks:
            if block.kind != "heading":
                continue
            page_idx = line_to_page.get(block.start_line)
            if page_idx is None:
                continue
            page_no = pages[page_idx].content_page_number if 0 <= page_idx < len(pages) else None
            normalized_heading_pages[(block.start_line, block.text)] = page_no or 1
        return LayoutResult(pages, line_to_page, normalized_heading_pages, warnings)


    def _measure_block_height(self, block: Block) -> float:
        if block.kind == "heading":
            style = self.styles[f"h{min(block.meta.get('level', 1), 6)}"]
            lines = self.measure.wrap_text(block.text, style, CONTENT_WIDTH_PT)
            return style.space_before + self.measure.text_height(lines, style) + style.space_after
        if block.kind == "paragraph":
            style = self.styles["body"]
            lines = self.measure.wrap_inline(self.measure.parse_inline(block.text), style, CONTENT_WIDTH_PT)
            return len(lines) * style.leading + style.space_after
        if block.kind == "list":
            style = self.styles["list"]
            h = 0.0
            for item in block.meta.get("items", []):
                max_w = max(40.0, CONTENT_WIDTH_PT - item.level * 18.0)
                lines, _ = self.measure.wrap_list_item(f"{item.marker if item.ordered else '•'} ", self.measure.parse_inline(item.text), style, max_w)
                h += len(lines) * style.leading + style.space_after
            return h
        if block.kind == "quote":
            inner_blocks = BlockParser(enable_special_markers=False).parse(block.text)
            inner_w = max(60.0, CONTENT_WIDTH_PT - (self.quote_gap_pt + self.quote_bar_pt + self.quote_padding_pt) * 2)
            _, inner_h, _ = self._layout_embedded_blocks(inner_blocks, PAGE_MARGIN_PT + self.quote_gap_pt + self.quote_bar_pt + self.quote_padding_pt, PAGE_MARGIN_PT + self.quote_padding_pt, inner_w)
            return max(self.quote_padding_pt * 2 + inner_h, 28.0) + 4
        if block.kind == "code":
            style = self.styles["code"]
            lines = block.text.splitlines() or [""]
            return 8 + len(lines) * style.leading + 12
        if block.kind in {"plantuml", "tex"}:
            try:
                path = self.assets.render_plantuml(block.text) if block.kind == "plantuml" else self.assets.render_tex(block.text)
                with Image.open(path) as img:
                    iw, ih = img.size
                max_w_px = pt_to_px(CONTENT_WIDTH_PT)
                max_h_px = pt_to_px(CONTENT_HEIGHT_PT * 0.5)
                natural_scale = 2.0 if block.kind == "plantuml" else 1.35
                scale = min(natural_scale, max_w_px / iw, max_h_px / ih)
                h_pt = ih * scale / self.assets.dpi * 72.0
                return h_pt + 8
            except Exception:
                return 24.0
        if block.kind == "image":
            try:
                path = self.assets.resolve_image(block.meta["src"])
                with Image.open(path) as img:
                    iw, ih = img.size
                max_w_px = pt_to_px(CONTENT_WIDTH_PT)
                max_h_px = pt_to_px(CONTENT_HEIGHT_PT * 0.7)
                scale = min(max_w_px / iw, max_h_px / ih, 1.0)
                return ih * scale / PREVIEW_DPI * 72.0 + 8
            except Exception:
                return 24.0
        if block.kind == "table":
            header, rows = self._parse_table(block.text)
            col_count = max(len(header), max((len(r) for r in rows), default=0))
            if not col_count:
                return 0.0
            header += [""] * (col_count - len(header))
            rows = [r + [""] * (col_count - len(r)) for r in rows]
            col_widths = self._compute_table_widths(header, rows, col_count)
            _, header_h = self._table_row_layout(header, col_widths, True)
            body_layout = [self._table_row_layout(r, col_widths, False) for r in rows]
            return header_h + sum(h for _, h in body_layout) + 8
        return 0.0

    def _layout_embedded_blocks(self, blocks: List[Block], x: float, y: float, width: float) -> Tuple[List[PageElement], float, List[str]]:
        elements: List[PageElement] = []
        warnings: List[str] = []
        start_y = y
        for block in blocks:
            if block.kind == "heading":
                style = self.styles[f"h{min(block.meta.get('level', 1),6)}"]
                if block.meta.get("align") == "center":
                    style = replace(style, align="center")
                lines = self.measure.wrap_text(block.text, style, width)
                y += style.space_before
                elements.append(TextElement(x, y, width, lines, style))
                y += self.measure.text_height(lines, style) + style.space_after
            elif block.kind == "paragraph":
                style = self.styles["body"]
                lines = self.measure.wrap_inline(self.measure.parse_inline(block.text), style, width)
                elements.append(RichTextElement(x, y, width, lines, style, [0.0] * len(lines)))
                y += len(lines) * style.leading + style.space_after
            elif block.kind == "list":
                style = self.styles["list"]
                for item in block.meta.get("items", []):
                    max_w = max(40.0, width - item.level * 18.0)
                    lines, indents = self.measure.wrap_list_item(f"{item.marker if item.ordered else '•'} ", self.measure.parse_inline(item.text), style, max_w)
                    elements.append(RichTextElement(x + item.level * 18.0, y, max_w, lines, style, indents))
                    y += len(lines) * style.leading + style.space_after
            elif block.kind == "quote":
                inner_x = x + self.quote_gap_pt + self.quote_bar_pt + self.quote_padding_pt
                inner_w = max(60.0, width - (self.quote_gap_pt + self.quote_bar_pt + self.quote_padding_pt) * 2)
                inner_blocks = BlockParser(enable_special_markers=False).parse(block.text)
                inner_elements, inner_h, inner_warn = self._layout_embedded_blocks(inner_blocks, inner_x, y + self.quote_padding_pt, inner_w)
                outer_h = max(self.quote_padding_pt * 2 + inner_h, 28.0)
                elements.append(QuoteBoxElement(x, y, width, outer_h, 1))
                elements.extend(inner_elements)
                warnings.extend(inner_warn)
                y += outer_h + 4
            elif block.kind == "code":
                style = self.styles["code"]
                lines = block.text.splitlines() or [""]
                elements.append(TextElement(x + 8, y + 8, width - 16, [ln if ln else " " for ln in lines], style))
                y += 8 + len(lines) * style.leading + 12
            elif block.kind == "image":
                try:
                    path = self.assets.resolve_image(block.meta["src"])
                    with Image.open(path) as img:
                        iw, ih = img.size
                    max_w_px = pt_to_px(width)
                    max_h_px = pt_to_px(CONTENT_HEIGHT_PT * 0.7)
                    scale = min(max_w_px / iw, max_h_px / ih, 1.0)
                    w_pt = iw * scale / PREVIEW_DPI * 72.0
                    h_pt = ih * scale / PREVIEW_DPI * 72.0
                    img_x = x + (width - w_pt) / 2
                    elements.append(ImageElement(img_x, y, w_pt, h_pt, path))
                    y += h_pt + 8
                except Exception as exc:
                    style = self.styles["code"]
                    lines = self.measure.wrap_text(f"[image render error: {exc}]", style, width)
                    elements.append(TextElement(x, y, width, lines, style))
                    y += self.measure.text_height(lines, style) + 6
                    warnings.append(str(exc))
            elif block.kind in {"plantuml", "tex"}:
                try:
                    path = self.assets.render_plantuml(block.text) if block.kind == "plantuml" else self.assets.render_tex(block.text)
                    with Image.open(path) as img:
                        iw, ih = img.size
                    max_w_px = pt_to_px(width)
                    max_h_px = pt_to_px(CONTENT_HEIGHT_PT * 0.5)
                    natural_scale = 2.0 if block.kind == "plantuml" else 1.35
                    scale = min(natural_scale, max_w_px / iw, max_h_px / ih)
                    w_pt = iw * scale / self.assets.dpi * 72.0
                    h_pt = ih * scale / self.assets.dpi * 72.0
                    img_x = x + (width - w_pt) / 2
                    elements.append(ImageElement(img_x, y, w_pt, h_pt, path))
                    y += h_pt + 8
                except Exception as exc:
                    style = self.styles["code"]
                    lines = self.measure.wrap_text(f"[{block.kind} render error: {exc}]", style, width)
                    elements.append(TextElement(x, y, width, lines, style))
                    y += self.measure.text_height(lines, style) + 6
                    warnings.append(str(exc))
            elif block.kind == "table":
                header, rows = self._parse_table(block.text)
                col_count = max(len(header), max((len(r) for r in rows), default=0))
                if not col_count:
                    continue
                header += [""] * (col_count - len(header))
                rows = [r + [""] * (col_count - len(r)) for r in rows]
                col_widths = self._compute_table_widths(header, rows, col_count, width)
                header_wrapped, header_h = self._table_row_layout(header, col_widths, True)
                body_layout = [self._table_row_layout(r, col_widths, False) for r in rows]
                header_lines = ["\n".join(c) for c in header_wrapped]
                rows_joined = [["\n".join(c) for c in wrapped] for wrapped, _ in body_layout]
                part_heights = [h for _, h in body_layout]
                table_width = min(sum(col_widths), width)
                table_x = x + max(0.0, (width - table_width) / 2.0)
                elements.append(TablePartElement(table_x, y, table_width, header_lines, rows_joined, col_widths, part_heights, header_h, block.start_line))
                y += header_h + sum(part_heights) + 8
        return elements, max(0.0, y - start_y), warnings

    def _layout_title_page(self, block: Block) -> PageLayout:
        page = PageLayout(section="title")
        page.first_source_line = block.start_line
        inner_blocks = BlockParser(enable_special_markers=False).parse(block.text)
        if inner_blocks and inner_blocks[0].kind == "heading" and inner_blocks[0].meta.get("level") == 1:
            inner_blocks[0].meta["align"] = "center"
        _, total_h, _ = self._layout_embedded_blocks(inner_blocks, PAGE_MARGIN_PT, PAGE_MARGIN_PT, CONTENT_WIDTH_PT)
        y = max(PAGE_MARGIN_PT, (PAGE_HEIGHT_PT - total_h) / 2.0)
        if total_h > CONTENT_HEIGHT_PT:
            y = PAGE_MARGIN_PT
        elements, _, _ = self._layout_embedded_blocks(inner_blocks, PAGE_MARGIN_PT, y, CONTENT_WIDTH_PT)
        page.elements.extend(elements)
        return page

    def _parse_table(self, text: str) -> Tuple[List[str], List[List[str]]]:
        rows = [line.strip() for line in text.splitlines()]
        if len(rows) < 2:
            return [], []
        def split(row: str) -> List[str]:
            row = row.strip()
            if row.startswith("|"):
                row = row[1:]
            if row.endswith("|"):
                row = row[:-1]
            return [cell.strip() for cell in row.split("|")]
        header = split(rows[0])
        body = [split(r) for r in rows[2:]]
        return header, body

    def _compute_table_widths(self, header: List[str], rows: List[List[str]], col_count: int, available_width: float = CONTENT_WIDTH_PT) -> List[float]:
        min_w = 48.0
        desired = [min_w] * col_count
        style = self.styles["body"]
        for idx in range(col_count):
            items = [header[idx]] + [r[idx] for r in rows if idx < len(r)]
            longest = max((self.measure.pt_width(cell[:60], style.font_name, style.font_size) for cell in items), default=min_w)
            desired[idx] = max(min_w, min(longest + 16, available_width / 1.5))
        total = sum(desired)
        if total <= available_width:
            return desired
        scale = available_width / total
        return [w * scale for w in desired]

    def _table_row_layout(self, row: List[str], col_widths: List[float], header: bool) -> Tuple[List[List[str]], float]:
        style = self.styles["h6"] if header else self.styles["body"]
        wrapped_cells: List[List[str]] = []
        max_h = 0.0
        for cell, width in zip(row, col_widths):
            lines = self.measure.wrap_text(cell, style, max(20, width - 10))
            wrapped_cells.append(lines)
            max_h = max(max_h, len(lines) * style.leading + 8)
        return wrapped_cells, max_h


class PageRenderer:
    def __init__(self, fonts: FontRegistry) -> None:
        self.fonts = fonts

    def render_pages(self, layout: LayoutResult) -> List[bytes]:
        result: List[bytes] = []
        for page in layout.pages:
            img = Image.new("RGB", (PREVIEW_PAGE_WIDTH_PX, PREVIEW_PAGE_HEIGHT_PX), "white")
            draw = ImageDraw.Draw(img)
            for el in page.elements:
                if isinstance(el, RichTextElement):
                    self._draw_rich_text(draw, el)
                elif isinstance(el, TextElement):
                    self._draw_text(draw, el)
                elif isinstance(el, ImageElement):
                    self._draw_image(img, el)
                elif isinstance(el, QuoteBoxElement):
                    self._draw_quote_box(draw, el)
                elif isinstance(el, TablePartElement):
                    self._draw_table(draw, el)
                elif isinstance(el, TOCEntryElement):
                    self._draw_toc_entry(draw, el)
            if page.content_page_number is not None:
                self._draw_page_number(draw, page.content_page_number)
            bio = tempfile.SpooledTemporaryFile()
            img.save(bio, format="PNG")
            bio.seek(0)
            result.append(bio.read())
        return result

    def _draw_text(self, draw: ImageDraw.ImageDraw, el: TextElement) -> None:
        x = pt_to_px(el.x)
        y = pt_to_px(el.y)
        width_px = pt_to_px(el.width)
        font_family = "mono" if el.style.font_name.startswith("DDMono") else "sans"
        font = self.fonts.pil_font(font_family, pt_to_px(el.style.font_size), bold=el.style.bold, italic=False)
        line_h = pt_to_px(el.style.leading)
        color = el.style.color
        for i, line in enumerate(el.lines):
            tx = x
            if el.style.align == "center":
                bbox = draw.textbbox((0, 0), line, font=font)
                tx = x + max(0, (width_px - (bbox[2] - bbox[0])) // 2)
            draw.text((tx, y + i * line_h), line, fill=color, font=font)

    def _draw_rich_text(self, draw: ImageDraw.ImageDraw, el: RichTextElement) -> None:
        x0 = pt_to_px(el.x)
        y0 = pt_to_px(el.y)
        line_h = pt_to_px(el.style.leading)
        for i, line in enumerate(el.lines):
            x = x0 + pt_to_px(el.line_indents[i] if i < len(el.line_indents) else 0.0)
            baseline_y = y0 + i * line_h
            for run in line:
                family = "mono" if run.code else "sans"
                font = self.fonts.pil_font(family, pt_to_px(el.style.font_size), bold=run.bold or el.style.bold, italic=run.italic)
                color = (29, 78, 216) if run.link else el.style.color
                bbox = draw.textbbox((x, baseline_y), run.text, font=font)
                width = bbox[2] - bbox[0]
                if run.code and run.text.strip():
                    pad_x = 2
                    pad_y = 1
                    draw.rounded_rectangle((x - pad_x, baseline_y - pad_y, x + width + pad_x, baseline_y + line_h - 1), radius=2, fill=(232, 232, 232))
                draw.text((x, baseline_y), run.text, fill=color, font=font)
                if run.link:
                    uy = baseline_y + line_h - 2
                    draw.line((x, uy, x + width, uy), fill=color, width=1)
                x += width

    def _draw_quote_box(self, draw: ImageDraw.ImageDraw, el: QuoteBoxElement) -> None:
        x = pt_to_px(el.x)
        y = pt_to_px(el.y)
        w = pt_to_px(el.width)
        h = pt_to_px(el.height)
        draw.rectangle((x, y, x + w, y + h), fill=(242, 242, 242), outline=(210, 210, 210), width=1)
        draw.rectangle((x, y, x + 5, y + h), fill=(0, 0, 0), outline=(0, 0, 0))

    def _draw_image(self, img: Image.Image, el: ImageElement) -> None:
        x = pt_to_px(el.x)
        y = pt_to_px(el.y)
        w = pt_to_px(el.width)
        h = pt_to_px(el.height)
        with Image.open(el.image_path).convert("RGBA") as src:
            resized = src.resize((w, h), Image.LANCZOS)
            img.paste(resized, (x, y), resized)

    def _draw_table(self, draw: ImageDraw.ImageDraw, el: TablePartElement) -> None:
        x = pt_to_px(el.x)
        y = pt_to_px(el.y)
        col_widths_px = [pt_to_px(w) for w in el.col_widths]
        header_font = self.fonts.pil_font("sans", pt_to_px(11), bold=True)
        body_font = self.fonts.pil_font("sans", pt_to_px(11), bold=False)
        header_h = pt_to_px(el.header_height)
        curr_y = y
        cx = x
        for title, w in zip(el.header, col_widths_px):
            draw.rectangle([cx, curr_y, cx + w, curr_y + header_h], outline="#6a6a6a", fill="#ececec")
            draw.multiline_text((cx + 4, curr_y + 4), title, fill="black", font=header_font, spacing=3)
            cx += w
        curr_y += header_h
        for row, row_h_pt in zip(el.rows, el.row_heights):
            row_h = pt_to_px(row_h_pt)
            cx = x
            for cell, w in zip(row, col_widths_px):
                draw.rectangle([cx, curr_y, cx + w, curr_y + row_h], outline="#808080", fill="white")
                draw.multiline_text((cx + 4, curr_y + 4), cell, fill="black", font=body_font, spacing=3)
                cx += w
            curr_y += row_h

    def _draw_toc_entry(self, draw: ImageDraw.ImageDraw, el: TOCEntryElement) -> None:
        x = pt_to_px(el.x)
        y = pt_to_px(el.y)
        width_px = pt_to_px(el.width)
        font = self.fonts.pil_font("sans", pt_to_px(el.style.font_size), bold=el.style.bold)
        line_h = pt_to_px(el.style.leading)
        number_text = str(el.page_number)
        nb = draw.textbbox((0, 0), number_text, font=font)
        number_w = nb[2] - nb[0]
        number_x = x + width_px - number_w
        draw.text((number_x, y), number_text, fill=el.style.color, font=font)
        first = el.title_lines[0] if el.title_lines else ""
        title_bbox = draw.textbbox((0, 0), first, font=font)
        title_w = title_bbox[2] - title_bbox[0]
        draw.text((x, y), first, fill=el.style.color, font=font)
        dot_y = y + line_h // 2 + 1
        dot_start = x + title_w + 8
        dot_end = number_x - 8
        if dot_end > dot_start:
            for px in range(dot_start, dot_end, 6):
                draw.ellipse((px, dot_y, px + 1, dot_y + 1), fill="#666666")
        for i, line in enumerate(el.title_lines[1:], start=1):
            draw.text((x, y + i * line_h), line, fill=el.style.color, font=font)

    def _draw_page_number(self, draw: ImageDraw.ImageDraw, number: int) -> None:
        font = self.fonts.pil_font("sans", pt_to_px(10), False)
        text = str(number)
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        draw.text(((PREVIEW_PAGE_WIDTH_PX - tw) // 2, PREVIEW_PAGE_HEIGHT_PX - pt_to_px(24)), text, fill="#333333", font=font)


class PDFExporter:
    def __init__(self, fonts: FontRegistry) -> None:
        self.fonts = fonts
        self.fonts.ensure_reportlab()

    def export_bytes(self, layout: LayoutResult) -> bytes:
        import io
        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=A4, pageCompression=1)
        for page in layout.pages:
            for el in page.elements:
                if isinstance(el, RichTextElement):
                    self._draw_rich_text(c, el)
                elif isinstance(el, TextElement):
                    self._draw_text(c, el)
                elif isinstance(el, ImageElement):
                    self._draw_image(c, el)
                elif isinstance(el, QuoteBoxElement):
                    self._draw_quote_box(c, el)
                elif isinstance(el, TablePartElement):
                    self._draw_table(c, el)
                elif isinstance(el, TOCEntryElement):
                    self._draw_toc_entry(c, el)
            if page.content_page_number is not None:
                c.setFont("DDSans", 10)
                c.drawCentredString(PAGE_WIDTH_PT / 2, PAGE_MARGIN_PT * 0.55, str(page.content_page_number))
            c.showPage()
        c.save()
        return buf.getvalue()

    def _draw_text(self, c: canvas.Canvas, el: TextElement) -> None:
        c.setFont(el.style.font_name, el.style.font_size)
        c.setFillColorRGB(*(v / 255 for v in el.style.color))
        for i, line in enumerate(el.lines):
            yy = PAGE_HEIGHT_PT - el.y - i * el.style.leading - el.style.font_size
            if el.style.align == "center":
                c.drawCentredString(el.x + el.width / 2, yy, line)
            else:
                c.drawString(el.x, yy, line)

    def _draw_rich_text(self, c: canvas.Canvas, el: RichTextElement) -> None:
        for i, line in enumerate(el.lines):
            x = el.x + (el.line_indents[i] if i < len(el.line_indents) else 0.0)
            yy = PAGE_HEIGHT_PT - el.y - i * el.style.leading - el.style.font_size
            for run in line:
                family = "mono" if run.code else "sans"
                font_name = self.fonts.reportlab_font_name(family, run.bold or el.style.bold, run.italic)
                c.setFont(font_name, el.style.font_size)
                w = pdfmetrics.stringWidth(run.text, font_name, el.style.font_size)
                if run.code and run.text.strip():
                    c.setFillColorRGB(232 / 255, 232 / 255, 232 / 255)
                    c.roundRect(x - 1.5, yy - 1.5, w + 3.0, el.style.leading, 2, fill=1, stroke=0)
                if run.link:
                    c.setFillColorRGB(29 / 255, 78 / 255, 216 / 255)
                else:
                    c.setFillColorRGB(*(v / 255 for v in el.style.color))
                c.drawString(x, yy, run.text)
                if run.link:
                    c.line(x, yy - 1, x + w, yy - 1)
                    c.linkURL(run.link, (x, yy - 2, x + w, yy + el.style.leading), relative=0)
                x += w

    def _draw_quote_box(self, c: canvas.Canvas, el: QuoteBoxElement) -> None:
        c.setStrokeColorRGB(210 / 255, 210 / 255, 210 / 255)
        c.setFillColorRGB(242 / 255, 242 / 255, 242 / 255)
        c.rect(el.x, PAGE_HEIGHT_PT - el.y - el.height, el.width, el.height, fill=1, stroke=1)
        c.setFillColorRGB(0, 0, 0)
        c.rect(el.x, PAGE_HEIGHT_PT - el.y - el.height, 5, el.height, fill=1, stroke=0)

    def _draw_image(self, c: canvas.Canvas, el: ImageElement) -> None:
        c.drawImage(ImageReader(el.image_path), el.x, PAGE_HEIGHT_PT - el.y - el.height, width=el.width, height=el.height, preserveAspectRatio=True, mask='auto')

    def _draw_table(self, c: canvas.Canvas, el: TablePartElement) -> None:
        x = el.x
        y = PAGE_HEIGHT_PT - el.y
        c.setStrokeColorRGB(0.5, 0.5, 0.5)
        c.setFillColorRGB(0.925, 0.925, 0.925)
        c.rect(x, y - el.header_height, sum(el.col_widths), el.header_height, fill=1, stroke=1)
        cx = x
        c.setFont("DDSans-Bold", 11)
        for title, w in zip(el.header, el.col_widths):
            c.rect(cx, y - el.header_height, w, el.header_height, fill=0, stroke=1)
            for idx, line in enumerate(title.splitlines() or [title]):
                c.drawString(cx + 4, y - 15 - idx * 12, line)
            cx += w
        y -= el.header_height
        c.setFont("DDSans", 11)
        for row, row_h in zip(el.rows, el.row_heights):
            cx = x
            for cell, w in zip(row, el.col_widths):
                c.rect(cx, y - row_h, w, row_h, fill=0, stroke=1)
                for idx, line in enumerate(cell.splitlines()):
                    c.drawString(cx + 4, y - 15 - idx * 12, line)
                cx += w
            y -= row_h

    def _draw_toc_entry(self, c: canvas.Canvas, el: TOCEntryElement) -> None:
        c.setFont(el.style.font_name, el.style.font_size)
        c.setFillColorRGB(*(v / 255 for v in el.style.color))
        line_h = el.style.leading
        number_text = str(el.page_number)
        number_w = pdfmetrics.stringWidth(number_text, el.style.font_name, el.style.font_size)
        number_x = el.x + el.width - number_w
        yy = PAGE_HEIGHT_PT - el.y - el.style.font_size
        first = el.title_lines[0] if el.title_lines else ""
        c.drawString(el.x, yy, first)
        c.drawString(number_x, yy, number_text)
        title_w = pdfmetrics.stringWidth(first, el.style.font_name, el.style.font_size)
        dot_start = el.x + title_w + 8
        dot_end = number_x - 8
        if dot_end > dot_start:
            c.saveState()
            c.setDash(1, 3)
            mid_y = PAGE_HEIGHT_PT - el.y - line_h / 2
            c.line(dot_start, mid_y, dot_end, mid_y)
            c.restoreState()
        for i, line in enumerate(el.title_lines[1:], start=1):
            yy = PAGE_HEIGHT_PT - el.y - i * line_h - el.style.font_size
            c.drawString(el.x, yy, line)


class DocumentRenderer:
    def __init__(self, plantuml_cmd: str = "plantuml", base_dir: Optional[str] = None) -> None:
        self.fonts = FontRegistry()
        self.assets = AssetRenderer(plantuml_cmd=plantuml_cmd, base_dir=base_dir)
        self.layout_engine = LayoutEngine(self.fonts, self.assets)
        self.page_renderer = PageRenderer(self.fonts)
        self.pdf_exporter = PDFExporter(self.fonts)

    def render(self, text: str) -> RenderResult:
        blocks = BlockParser().parse(text)
        layout = self.layout_engine.layout(blocks)
        pngs = self.page_renderer.render_pages(layout)
        pdf = self.pdf_exporter.export_bytes(layout)
        return RenderResult(page_pngs=pngs, pdf_bytes=pdf, line_to_page=layout.line_to_page, warnings=layout.warnings)


# ---------------- GUI ----------------
if PYSIDE_AVAILABLE:
    class RenderSignals(QObject):
        done = Signal(int, object)
        failed = Signal(int, str)


    class RenderJob(QRunnable):
        def __init__(self, generation: int, text: str, plantuml_cmd: str, base_dir: str) -> None:
            super().__init__()
            self.generation = generation
            self.text = text
            self.plantuml_cmd = plantuml_cmd
            self.base_dir = base_dir
            self.signals = RenderSignals()

        def run(self) -> None:
            try:
                result = DocumentRenderer(self.plantuml_cmd, self.base_dir).render(self.text)
                self.signals.done.emit(self.generation, result)
            except Exception as exc:
                self.signals.failed.emit(self.generation, str(exc))


    class MarkdownHighlighter(QSyntaxHighlighter):
        def __init__(self, document) -> None:
            super().__init__(document)
            self.formats: Dict[str, QTextCharFormat] = {}
            for name, color, bold in [
                ("heading", "#1f4d8f", True),
                ("fence", "#7a3aa6", True),
                ("table", "#915500", False),
                ("code", "#2f2f2f", False),
                ("kw", "#1f4d8f", True),
                ("comment", "#6a6a6a", False),
                ("string", "#8a2d2d", False),
            ]:
                fmt = QTextCharFormat()
                fmt.setForeground(QColor(color))
                if bold:
                    fmt.setFontWeight(QFont.Bold)
                self.formats[name] = fmt
            self.lexers = {"python": PythonLexer(), "py": PythonLexer(), "c": CLexer(), "cpp": CppLexer(), "c++": CppLexer(), "h": CppLexer(), "hpp": CppLexer(), "java": JavaLexer(), "tex": TexLexer()}
            self.puml_words = {"@startuml", "@enduml", "participant", "actor", "component", "database", "package", "note", "skinparam", "rectangle"}

        def _prev_block_info(self) -> Tuple[bool, str, str]:
            prev = self.currentBlock().previous()
            if not prev.isValid():
                return False, "", ""
            data = prev.userData()
            if data is None:
                return False, "", ""
            return bool(getattr(data, "inside_code", False)), getattr(data, "lang", "") or "", getattr(data, "fence", "") or ""

        def highlightBlock(self, text: str) -> None:
            prev_inside, prev_lang, prev_fence = self._prev_block_info()
            m = FENCE_RE.match(text)
            if prev_inside:
                if m and (m.group(1) == prev_fence):
                    self.setFormat(0, len(text), self.formats["fence"])
                    self.setCurrentBlockState(0)
                    self._set_block_userdata(False, "", "")
                    return
                self.setCurrentBlockState(1)
                self._set_block_userdata(True, prev_lang, prev_fence)
                self._highlight_code(text, prev_lang)
                return

            if m:
                fence = m.group(1)
                lang = (m.group(2) or "").strip().lower()
                self.setFormat(0, len(text), self.formats["fence"])
                self.setCurrentBlockState(1)
                self._set_block_userdata(True, lang, fence)
                return

            self.setCurrentBlockState(0)
            self._set_block_userdata(False, "", "")
            if text.lstrip().startswith("#"):
                self.setFormat(0, len(text), self.formats["heading"])
            elif "|" in text:
                self.setFormat(0, len(text), self.formats["table"])

        def _set_block_userdata(self, inside_code: bool, lang: str, fence: str) -> None:
            block = self.currentBlock()
            block.setUserState(1 if inside_code else 0)
            data = block.userData()
            if data is None:
                from PySide6.QtGui import QTextBlockUserData
                data = QTextBlockUserData()
                block.setUserData(data)
            setattr(data, "inside_code", inside_code)
            setattr(data, "lang", lang)
            setattr(data, "fence", fence)

        def _highlight_code(self, text: str, lang: str) -> None:
            lexer = self.lexers.get(lang)
            if lexer:
                pos = 0
                for tok, val in lex(text, lexer):
                    length = len(val)
                    if tok in Token.Keyword:
                        self.setFormat(pos, length, self.formats["kw"])
                    elif tok in Token.Literal.String:
                        self.setFormat(pos, length, self.formats["string"])
                    elif tok in Token.Comment:
                        self.setFormat(pos, length, self.formats["comment"])
                    pos += length
                return
            if lang in {"plantuml", "puml"}:
                for m in re.finditer(r"\S+", text):
                    if m.group(0) in self.puml_words:
                        self.setFormat(m.start(), len(m.group(0)), self.formats["kw"])


    class LineNumberArea(QWidget):
        def __init__(self, editor: 'CodeEditor') -> None:
            super().__init__(editor)
            self.code_editor = editor

        def sizeHint(self):
            return QSize(self.code_editor.lineNumberAreaWidth(), 0)

        def paintEvent(self, event):
            self.code_editor.line_number_area_paint_event(event)


    def completions_for_lang(lang: str) -> List[str]:
        return {
            "plantuml": ["@startuml", "@enduml", "participant", "actor", "component", "package", "database", "note left", "note right", "left to right direction", "skinparam", "rectangle"],
            "puml": ["@startuml", "@enduml", "participant", "actor", "component", "package", "database", "note left", "note right", "left to right direction", "skinparam", "rectangle"],
            "tex": ["\\frac{}{}", "\\sqrt{}", "\\sum_{i=1}^{n}", "\\int_{a}^{b}", "\\alpha", "\\beta", "\\gamma", "\\begin{bmatrix}", "\\end{bmatrix}"],
            "python": ["def", "class", "import", "from", "if", "elif", "else", "for", "while", "return", "with"],
            "py": ["def", "class", "import", "from", "if", "elif", "else", "for", "while", "return", "with"],
            "c": ["#include", "int", "char", "void", "struct", "typedef", "static", "const", "return"],
            "cpp": ["#include", "class", "struct", "namespace", "template", "std::", "constexpr", "return"],
            "c++": ["#include", "class", "struct", "namespace", "template", "std::", "constexpr", "return"],
            "h": ["#pragma once", "#include", "struct", "class", "typedef"],
            "hpp": ["#pragma once", "#include", "struct", "class", "namespace", "template"],
            "java": ["class", "public", "private", "protected", "static", "void", "import", "package", "return"],
        }.get(lang.lower(), [])


    class CodeEditor(QPlainTextEdit):
        previewSyncRequested = Signal(int)

        def __init__(self) -> None:
            super().__init__()
            self.setFont(QFont("DejaVu Sans Mono", 11))
            self.setLineWrapMode(QPlainTextEdit.NoWrap)
            self.setWordWrapMode(QTextOption.NoWrap)
            self.setTabStopDistance(self.fontMetrics().horizontalAdvance(" ") * 8)
            self._line_number_area = LineNumberArea(self)
            self.blockCountChanged.connect(self._update_line_number_area_width)
            self.updateRequest.connect(self._update_line_number_area)
            self.cursorPositionChanged.connect(self._cursor_changed)
            self._update_line_number_area_width(0)
            self.highlighter = MarkdownHighlighter(self.document())
            self.completer = QCompleter([], self)
            self.completer.setCompletionMode(QCompleter.PopupCompletion)
            self.completer.setCaseSensitivity(Qt.CaseInsensitive)
            self.completer.setWidget(self)
            self.completer.activated.connect(self.insert_completion)

        def lineNumberAreaWidth(self) -> int:
            digits = max(2, len(str(max(1, self.blockCount()))))
            return 10 + self.fontMetrics().horizontalAdvance("9") * digits

        def _update_line_number_area_width(self, _count: int) -> None:
            self.setViewportMargins(self.lineNumberAreaWidth(), 0, 0, 0)

        def _update_line_number_area(self, rect, dy) -> None:
            if dy:
                self._line_number_area.scroll(0, dy)
            else:
                self._line_number_area.update(0, rect.y(), self._line_number_area.width(), rect.height())

        def resizeEvent(self, event) -> None:
            super().resizeEvent(event)
            cr = self.contentsRect()
            self._line_number_area.setGeometry(QRect(cr.left(), cr.top(), self.lineNumberAreaWidth(), cr.height()))

        def line_number_area_paint_event(self, event) -> None:
            painter = QPainter(self._line_number_area)
            painter.fillRect(event.rect(), QColor("#f4f4f4"))
            block = self.firstVisibleBlock()
            block_number = block.blockNumber()
            top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
            bottom = top + round(self.blockBoundingRect(block).height())
            current = self.textCursor().blockNumber()
            while block.isValid() and top <= event.rect().bottom():
                if block.isVisible() and bottom >= event.rect().top():
                    painter.setPen(QColor("#1f4d8f") if block_number == current else QColor("#505050"))
                    painter.drawText(0, top, self._line_number_area.width() - 6, self.fontMetrics().height(), Qt.AlignRight, str(block_number + 1))
                block = block.next()
                top = bottom
                bottom = top + round(self.blockBoundingRect(block).height())
                block_number += 1


        def keyPressEvent(self, event) -> None:
            if event.key() == Qt.Key_Tab and event.modifiers() == Qt.NoModifier:
                self.insertPlainText(" " * 8)
                return
            if event.key() == Qt.Key_Space and event.modifiers() & Qt.ControlModifier:
                self.show_completions()
                return
            if event.key() in (Qt.Key_Return, Qt.Key_Enter) and self.completer.popup().isVisible():
                current_index = self.completer.popup().selectionModel().currentIndex()
                if not current_index.isValid():
                    current_index = self.completer.popup().currentIndex()
                current = current_index.data() if current_index.isValid() else None
                if current:
                    event.accept()
                    self.insert_completion(str(current))
                    self.completer.popup().hide()
                    return
            if event.key() in (Qt.Key_Return, Qt.Key_Enter) and event.modifiers() == Qt.ControlModifier:
                self.insertPlainText("\n<!-- pagebreak -->\n")
                return
            if event.key() in (Qt.Key_Return, Qt.Key_Enter) and event.modifiers() == Qt.ShiftModifier:
                self.insertPlainText("\n")
                return
            super().keyPressEvent(event)

        def current_block_language(self) -> Optional[str]:
            data = self.textCursor().block().userData()
            if data is None or not getattr(data, "inside_code", False):
                return None
            return getattr(data, "lang", None)

        def show_completions(self) -> None:
            lang = self.current_block_language()
            if not lang:
                return
            prefix = self.textCursor().block().text()[: self.textCursor().positionInBlock()]
            token = re.split(r"[^A-Za-z_\\@]+", prefix)[-1]
            suggestions = completions_for_lang(lang)
            if token:
                suggestions = [s for s in suggestions if s.startswith(token)] or suggestions
            self.completer.setModel(QStringListModel(suggestions, self.completer))
            rect = self.cursorRect()
            rect.setWidth(260)
            self.completer.complete(rect)

        def insert_completion(self, completion: str) -> None:
            tc = self.textCursor()
            prefix = re.split(r"[^A-Za-z_\\@]+", tc.block().text()[: tc.positionInBlock()])[-1]
            for _ in range(len(prefix)):
                tc.deletePreviousChar()
            tc.insertText(completion)
            self.setTextCursor(tc)

        def _cursor_changed(self) -> None:
            self.previewSyncRequested.emit(self.textCursor().blockNumber() + 1)

        def scroll_to_line(self, line_no: int, set_cursor: bool = False) -> None:
            block = self.document().findBlockByNumber(max(0, line_no - 1))
            if block.isValid():
                cursor = QTextCursor(block)
                if set_cursor:
                    self.setTextCursor(cursor)
                else:
                    self.blockSignals(True)
                    self.setTextCursor(cursor)
                    self.blockSignals(False)
                self.centerCursor()


    class PreviewScrollArea(QScrollArea):
        zoomChanged = Signal(float)

        def __init__(self) -> None:
            super().__init__()
            self._zoom_factor = 1.0
            self.setWidgetResizable(False)

        def zoom_factor(self) -> float:
            return self._zoom_factor

        def set_zoom_factor(self, value: float, anchor_pos=None) -> None:
            value = max(PREVIEW_ZOOM_MIN, min(PREVIEW_ZOOM_MAX, float(value)))
            if abs(value - self._zoom_factor) < 1e-6:
                return

            old_zoom = self._zoom_factor
            self._zoom_factor = value

            widget = self.widget()
            if widget is not None and hasattr(widget, "set_zoom_factor"):
                widget.set_zoom_factor(self._zoom_factor)

            self.zoomChanged.emit(self._zoom_factor)

            if anchor_pos is not None:
                hbar = self.horizontalScrollBar()
                vbar = self.verticalScrollBar()
                scene_x = hbar.value() + anchor_pos.x()
                scene_y = vbar.value() + anchor_pos.y()
                ratio = self._zoom_factor / old_zoom if old_zoom > 0 else 1.0
                hbar.setValue(int(scene_x * ratio - anchor_pos.x()))
                vbar.setValue(int(scene_y * ratio - anchor_pos.y()))

        def resizeEvent(self, event) -> None:
            super().resizeEvent(event)
            widget = self.widget()
            if widget is not None and hasattr(widget, "set_viewport_width"):
                widget.set_viewport_width(self.viewport().width())

        def wheelEvent(self, event) -> None:
            mods = event.modifiers()
            if mods & Qt.ControlModifier:
                delta = event.angleDelta().y()
                if delta:
                    step = PREVIEW_ZOOM_STEP if delta > 0 else (1.0 / PREVIEW_ZOOM_STEP)
                    self.set_zoom_factor(self._zoom_factor * step, event.position().toPoint())
                event.accept()
                return

            if mods & Qt.ShiftModifier:
                amount = event.angleDelta().y() or event.angleDelta().x()
                self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - amount)
                event.accept()
                return

            super().wheelEvent(event)


    class PreviewWidget(QWidget):
        pageClicked = Signal(int)
        previewCenteredAtPage = Signal(int)

        def __init__(self) -> None:
            super().__init__()
            self.page_pixmaps: List[QPixmap] = []
            self.page_rects: List[QRect] = []
            self._viewport_width = 500
            self._zoom_factor = 1.0
            self._fit_zoom_factor = 1.0
            self._user_zoomed = False
            self.setMinimumWidth(360)

        def set_viewport_width(self, width: int) -> None:
            self._viewport_width = max(100, width)
            self._recompute_fit_zoom()
            self._refresh_geometry()

        def set_zoom_factor(self, value: float) -> None:
            self._zoom_factor = max(PREVIEW_ZOOM_MIN, min(PREVIEW_ZOOM_MAX, float(value)))
            self._user_zoomed = True
            self._refresh_geometry()

        def _recompute_fit_zoom(self) -> None:
            if not self.page_pixmaps:
                self._fit_zoom_factor = 1.0
                return
            max_source_w = max(p.width() for p in self.page_pixmaps)
            available = max(100, self._viewport_width - 40 - PAGE_SHADOW_PX)
            self._fit_zoom_factor = max(PREVIEW_ZOOM_MIN, min(1.0, available / max_source_w))
            if not self._user_zoomed:
                self._zoom_factor = self._fit_zoom_factor

        def reset_fit_zoom(self) -> None:
            self._user_zoomed = False
            self._recompute_fit_zoom()
            self._refresh_geometry()

        def _page_scale(self) -> float:
            if not self.page_pixmaps:
                return 1.0
            return max(0.05, self._zoom_factor)

        def _content_width(self) -> int:
            if not self.page_pixmaps:
                return max(420, self._viewport_width)
            scale = self._page_scale()
            max_w = max(int(p.width() * scale) for p in self.page_pixmaps)
            return max_w + 30 + PAGE_SHADOW_PX * 2

        def _content_height(self) -> int:
            scale = self._page_scale()
            total = 15
            for pix in self.page_pixmaps:
                total += int(pix.height() * scale) + PAGE_GAP_PX
            return total + 10

        def _refresh_geometry(self) -> None:
            self.setMinimumWidth(self._content_width())
            self.setMinimumHeight(self._content_height())
            self.resize(self.sizeHint())
            self.updateGeometry()
            self.update()

        def sizeHint(self):
            return QSize(self._content_width(), self._content_height())

        def set_pages(self, images: List[bytes]) -> None:
            self.page_pixmaps = []
            for data in images:
                pix = QPixmap()
                pix.loadFromData(data, "PNG")
                self.page_pixmaps.append(pix)
            self._recompute_fit_zoom()
            self._refresh_geometry()

        def resizeEvent(self, event) -> None:
            super().resizeEvent(event)
            self.update()

        def paintEvent(self, event) -> None:
            painter = QPainter(self)
            painter.fillRect(self.rect(), QColor("#cfcfcf"))
            self.page_rects = []
            y = 15
            scale = self._page_scale()
            for pix in self.page_pixmaps:
                w = int(pix.width() * scale)
                h = int(pix.height() * scale)
                x = max(15, (self.width() - w) // 2)
                for offset, alpha in [(6, 20), (4, 30), (2, 42)]:
                    painter.fillRect(QRect(x + offset, y + offset, w, h), QColor(0, 0, 0, alpha))
                rect = QRect(x, y, w, h)
                painter.fillRect(rect, Qt.white)
                painter.setPen(QColor("#7a7a7a"))
                painter.drawRect(rect)
                if scale != 1.0:
                    painter.drawPixmap(rect, pix.scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                else:
                    painter.drawPixmap(rect, pix)
                self.page_rects.append(rect)
                y += h + PAGE_GAP_PX

        def mousePressEvent(self, event) -> None:
            for idx, rect in enumerate(self.page_rects):
                if rect.contains(event.pos()):
                    self.pageClicked.emit(idx)
                    return
            super().mousePressEvent(event)

        def scroll_to_page_middle(self, page_index: int) -> None:
            parent = self.parentWidget()
            while parent and not isinstance(parent, QScrollArea):
                parent = parent.parentWidget()
            if not isinstance(parent, QScrollArea):
                return
            if 0 <= page_index < len(self.page_rects):
                rect = self.page_rects[page_index]
                target = rect.center().y() - parent.viewport().height() // 2
                parent.verticalScrollBar().setValue(max(0, target))


    class MainWindow(QMainWindow):
        def __init__(self, file_path: Optional[str] = None, plantuml_cmd: str = "plantuml") -> None:
            super().__init__()
            self.setWindowTitle(APP_NAME)
            self.resize(1400, 900)
            self.file_path = file_path
            self.plantuml_cmd = plantuml_cmd
            self.thread_pool = QThreadPool.globalInstance()
            self.generation = 0
            self.last_result: Optional[RenderResult] = None
            self.line_to_page: Dict[int, int] = {}
            self.page_first_lines: Dict[int, int] = {}

            self.editor = CodeEditor()
            self.preview = PreviewWidget()
            self.scroll = PreviewScrollArea()
            self.scroll.setWidget(self.preview)
            self.preview.set_viewport_width(self.scroll.viewport().width())
            self.status = QLabel()
            self.status.setFixedHeight(24)
            self.status.setStyleSheet("padding: 2px 8px; color: #333; background: #efefef; border-top: 1px solid #d0d0d0;")
            self._suspend_preview_sync = False

            splitter = QSplitter()
            splitter.addWidget(self.editor)
            splitter.addWidget(self.scroll)
            splitter.setStretchFactor(0, 2)
            splitter.setStretchFactor(1, 1)
            splitter.setSizes([900, 500])
            splitter.setChildrenCollapsible(False)

            central = QWidget()
            layout = QVBoxLayout(central)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.addWidget(splitter)
            layout.addWidget(self.status)
            self.setCentralWidget(central)

            self._build_toolbar()
            self.timer = QTimer(self)
            self.timer.setSingleShot(True)
            self.timer.setInterval(350)
            self.timer.timeout.connect(self.schedule_render)
            self.editor.textChanged.connect(self.timer.start)

            if self.file_path and os.path.exists(self.file_path):
                self.editor.setPlainText(Path(self.file_path).read_text(encoding="utf-8"))
            else:
                self.editor.setPlainText("# Title: Example\n\nA short subtitle or intro text.\n\n# TOC\n\n# Section\n\nText here.\n")
            self.schedule_render()

        def _build_toolbar(self) -> None:
            tb = QToolBar()
            self.addToolBar(tb)
            open_act = QAction("Open", self)
            save_act = QAction("Save", self)
            export_act = QAction("Export PDF", self)
            open_act.triggered.connect(self.open_file)
            save_act.triggered.connect(self.save_file)
            export_act.triggered.connect(self.export_pdf)
            save_act.setShortcut(QKeySequence.Save)
            tb.addAction(open_act)
            tb.addAction(save_act)
            tb.addAction(export_act)

        def open_file(self) -> None:
            path, _ = QFileDialog.getOpenFileName(self, "Open Markdown", "", "Markdown (*.md *.markdown);;All files (*)")
            if not path:
                return
            self.file_path = path
            self.editor.setPlainText(Path(path).read_text(encoding="utf-8"))
            self.schedule_render()

        def save_file(self) -> None:
            path = self.file_path
            if not path:
                path, _ = QFileDialog.getSaveFileName(self, "Save Markdown", "document.md", "Markdown (*.md)")
                if not path:
                    return
                self.file_path = path
            Path(path).write_text(self.editor.toPlainText(), encoding="utf-8")
            self.status.setText(f"Saved: {path}")

        def export_pdf(self) -> None:
            path, _ = QFileDialog.getSaveFileName(self, "Export PDF", "document.pdf", "PDF (*.pdf)")
            if not path:
                return
            try:
                result = DocumentRenderer(self.plantuml_cmd).render(self.editor.toPlainText())
                Path(path).write_bytes(result.pdf_bytes)
                self.status.setText(f"Exported: {path}")
            except Exception as exc:
                QMessageBox.critical(self, APP_NAME, str(exc))

        def schedule_render(self) -> None:
            self.generation += 1
            gen = self.generation
            text = self.editor.toPlainText()
            self.status.setText("Rendering preview…")
            base_dir = str(Path(self.file_path).resolve().parent) if self.file_path else os.getcwd()
            job = RenderJob(gen, text, self.plantuml_cmd, base_dir)
            job.signals.done.connect(self._render_done)
            job.signals.failed.connect(self._render_failed)
            self.thread_pool.start(job)

        def _render_done(self, generation: int, result: RenderResult) -> None:
            if generation != self.generation:
                return
            self.last_result = result
            self.line_to_page = result.line_to_page
            self.preview.set_pages(result.page_pngs)
            self.status.setText("Preview ready" + (f" — {result.warnings[0]}" if result.warnings else ""))

        def _render_failed(self, generation: int, message: str) -> None:
            if generation != self.generation:
                return
            self.status.setText(f"Render failed: {message}")

        def sync_preview_from_cursor(self, line_no: int) -> None:
            if self._suspend_preview_sync:
                return
            page_index = self.line_to_page.get(line_no)
            if page_index is None:
                candidates = [ln for ln in self.line_to_page if ln <= line_no]
                page_index = self.line_to_page[max(candidates)] if candidates else 0
            QTimer.singleShot(0, lambda: self.preview.scroll_to_page_middle(page_index))

        def _page_at_scroll_center(self) -> Optional[int]:
            if not self.preview.page_rects:
                return None
            center_y = self.scroll.verticalScrollBar().value() + self.scroll.viewport().height() // 2
            best_idx = None
            best_dist = 10**9
            for idx, rect in enumerate(self.preview.page_rects):
                dist = abs(rect.center().y() - center_y)
                if dist < best_dist:
                    best_idx = idx
                    best_dist = dist
            return best_idx

        def sync_editor_from_preview_scroll(self) -> None:
            page = self._page_at_scroll_center()
            if page is None:
                return
            for ln, pg in sorted(self.line_to_page.items()):
                if pg == page:
                    self._suspend_preview_sync = True
                    self.editor.scroll_to_line(ln, set_cursor=False)
                    QTimer.singleShot(EDITOR_SYNC_GUARD_MS, lambda: setattr(self, "_suspend_preview_sync", False))
                    break

        def sync_editor_from_preview_click(self, page_index: int) -> None:
            for ln, pg in sorted(self.line_to_page.items()):
                if pg == page_index:
                    self.editor.scroll_to_line(ln, set_cursor=True)
                    break


# --------------- CLI ----------------
def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=APP_NAME)
    p.add_argument("markdown", nargs="?", help="Markdown file to open or export")
    p.add_argument("-o", "--output", help="Output PDF path for --export-pdf")
    p.add_argument("--export-pdf", action="store_true", help="Export the selected Markdown file to PDF without starting the GUI")
    p.add_argument("--plantuml-cmd", default=bundled_plantuml_cmd() or "plantuml", help="PlantUML executable command")
    return p


def run_cli_export(input_path: str, output_path: Optional[str], plantuml_cmd: str) -> int:
    if not input_path:
        print("error: input markdown file is required for --export-pdf", file=sys.stderr)
        return 2
    source = Path(input_path)
    if not source.exists():
        print(f"error: file not found: {input_path}", file=sys.stderr)
        return 2
    target = Path(output_path) if output_path else source.with_suffix(".pdf")
    try:
        result = DocumentRenderer(plantuml_cmd).render(source.read_text(encoding="utf-8"))
        target.write_bytes(result.pdf_bytes)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"Exported PDF: {target}")
    if result.warnings:
        for w in result.warnings:
            print(f"warning: {w}", file=sys.stderr)
    return 0


def run_gui(file_path: Optional[str], plantuml_cmd: str) -> int:
    if not PYSIDE_AVAILABLE:
        print("error: PySide6 is not installed. Install requirements and try again.", file=sys.stderr)
        return 1
    app = QApplication(sys.argv)
    win = MainWindow(file_path, plantuml_cmd=plantuml_cmd)
    win.show()
    return app.exec()


def main() -> int:
    args = build_arg_parser().parse_args()
    if args.export_pdf:
        return run_cli_export(args.markdown, args.output, args.plantuml_cmd)
    return run_gui(args.markdown, args.plantuml_cmd)


if __name__ == "__main__":
    raise SystemExit(main())
