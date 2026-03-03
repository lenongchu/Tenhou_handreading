"""
麻将牌示意图生成器

根据舍牌/副露符号（如 4mc3m5m）生成示意图图片，用于展示分析场景。
使用自定义立体空白牌体 + FluffyStuff 字符图叠加，风格统一专业。
"""

import re
from pathlib import Path
from typing import List, Tuple, Optional, Dict

from .equivalent_variants import split_discard_pattern

# PyQt5 统一导入，避免各函数内遗漏
from PyQt5.QtCore import Qt, QPointF, QRectF, QRect
from PyQt5.QtGui import (
    QImage, QPainter, QColor, QPen, QBrush, QFont,
    QLinearGradient, QPixmap, QTransform, QPolygonF, QPainterPath,
)

# 空白牌体、3D 渲染缓存（避免重复生成）
_BLANK_TILE_CACHE: Dict[Tuple[int, int], QImage] = {}
_CHAR_IMG_CACHE: Dict[str, QImage] = {}
_MODEL_TILE_CACHE: Dict[Tuple[int, int], QImage] = {}
_TILE_3D_CACHE: Dict[Tuple[str, int, int], QImage] = {}


def _draw_table_background(
    painter: "QPainter",
    width: int,
    height: int,
    bg_color: Tuple[int, int, int],
) -> None:
    """Draw a felt-like table background."""
    base = QColor(*bg_color)
    if bg_color == (220, 235, 255):
        c0 = QColor(7, 64, 73)
        c1 = QColor(7, 85, 96)
        c2 = QColor(5, 50, 58)
    else:
        c0 = base.darker(230)
        c1 = base.darker(170)
        c2 = base.darker(260)

    grad = QLinearGradient(0, 0, width, height)
    grad.setColorAt(0.0, c0)
    grad.setColorAt(0.5, c1)
    grad.setColorAt(1.0, c2)
    painter.fillRect(QRectF(0, 0, width, height), QBrush(grad))

    top_glow = QLinearGradient(0, 0, 0, max(1, int(height * 0.28)))
    top_glow.setColorAt(0.0, QColor(80, 195, 210, 110))
    top_glow.setColorAt(1.0, QColor(80, 195, 210, 0))
    painter.fillRect(QRectF(0, 0, width, int(height * 0.28)), QBrush(top_glow))

    painter.setPen(QPen(QColor(0, 0, 0, 20), 1))
    step = max(10, height // 24)
    for y in range(step, height, step):
        painter.drawLine(0, y, width, y)


def _create_blank_tile(width: int, height: int) -> "QImage":
    """
    创建立体风格的空白牌体（象牙白、3D 渐变、圆角边框）。
    """
    key = (width, height)
    if key in _BLANK_TILE_CACHE:
        return _BLANK_TILE_CACHE[key].copy()
    img = QImage(width, height, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    rx, ry = max(2, width // 12), max(2, height // 12)
    rect = QRectF(1, 1, width - 2, height - 2)
    # 主体渐变：左上亮、右下暗，模拟 3D 厚度
    grad = QLinearGradient(0, 0, width, height)
    grad.setColorAt(0, QColor(255, 252, 248))
    grad.setColorAt(0.3, QColor(253, 248, 238))
    grad.setColorAt(0.7, QColor(248, 242, 228))
    grad.setColorAt(1, QColor(238, 230, 218))
    p.setBrush(QBrush(grad))
    p.setPen(QPen(QColor(200, 190, 175), 1))
    p.drawRoundedRect(rect, rx, ry)
    p.end()
    _BLANK_TILE_CACHE[key] = img.copy()
    return img


# 牌面图片映射：our notation -> FluffyStuff repo filename (不含扩展名)
TILE_TO_FILENAME: Dict[str, str] = {}
for i in range(1, 10):
    TILE_TO_FILENAME[f"{i}m"] = f"Man{i}"
    TILE_TO_FILENAME[f"{i}p"] = f"Pin{i}"
    TILE_TO_FILENAME[f"{i}s"] = f"Sou{i}"
TILE_TO_FILENAME["0m"] = "Man5-Dora"
TILE_TO_FILENAME["0p"] = "Pin5-Dora"
TILE_TO_FILENAME["0s"] = "Sou5-Dora"
# 字牌 1z-7z: 东南西北白发中 -> Ton, Nan, Shaa, Pei, Haku, Hatsu, Chun
TILE_TO_FILENAME.update({
    "1z": "Ton", "2z": "Nan", "3z": "Shaa", "4z": "Pei",
    "5z": "Haku", "6z": "Hatsu", "7z": "Chun",
})
# 中文 -> 文件名（直接查图用）
CN_TO_FILENAME = {"东": "Ton", "南": "Nan", "西": "Shaa", "北": "Pei", "白": "Haku", "發": "Hatsu", "发": "Hatsu", "中": "Chun"}

# 牌面资源目录：SVG 矢量源（立体效果好）优先，PNG 回退
def _tiles_svg_dir() -> Path:
    return Path(__file__).parent.parent / "assets" / "riichi-mahjong-tiles" / "Regular"

def _tiles_png_dir() -> Path:
    return Path(__file__).parent.parent / "assets" / "riichi-mahjong-tiles" / "Export" / "Regular"


def _tile_3d_bake_dir() -> Path:
    return Path(__file__).parent.parent / "assets" / "3d-tile"


def _load_baked_3d_tile(tile_str: str, width: int, height: int) -> Optional["QImage"]:
    """
    加载预烘焙 3D 牌图（bake_3d_tiles.py 生成）。
    存在则用，否则返回 None 回退到 2D 绘制。
    """
    key = (tile_str, int(width), int(height))
    if key in _MODEL_TILE_CACHE:
        return _MODEL_TILE_CACHE[key].copy()
    dir_ = _tile_3d_bake_dir()
    if not dir_.exists():
        return None
    if tile_str == "_":
        name = "back"
    elif tile_str == "?":
        name = "unknown"
    else:
        name = TILE_TO_FILENAME.get(tile_str) or ""
    if not name:
        return None
    path = dir_ / f"{name}.png"
    if not path.exists():
        return None
    img = QImage(str(path.resolve()))
    if img.isNull():
        return None
    scaled = img.scaled(int(width), int(height), Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    _MODEL_TILE_CACHE[key] = scaled.copy()
    return scaled


def _glyph_search_dirs() -> List[Path]:
    """
    Optional glyph-only assets (transparent foreground only).
    We check a few common folder layouts to keep integration simple.
    """
    root = Path(__file__).parent.parent / "assets"
    return [
        root / "tile-glyphs",
        root / "tile-glyphs" / "png",
        root / "tile-glyphs" / "svg",
        root / "mahjong-glyphs",
        root / "mahjong-glyphs" / "png",
        root / "mahjong-glyphs" / "svg",
    ]

CREDIT_TEXT = "Tiles: FluffyStuff/riichi-mahjong-tiles"


def parse_chi_notation(notation: str) -> Optional[Tuple[str, str, str]]:
    """
    解析吃的符号，如 4mc3m5m 或 1sc2s3s。
    返回 (consumed, left, right)，例如 4mc3m5m -> ("4m", "3m", "5m")。
    """
    notation = notation.strip().lower()
    if "c" not in notation:
        return None
    idx = notation.index("c")
    if idx > 0:
        consumed = notation[:idx]
        rest = notation[idx + 1:]
        parts = re.findall(r"[0-9][mps]", rest)
        if len(parts) == 2 and re.match(r"^[0-9][mps]$", consumed):
            return (consumed, parts[0], parts[1])
    else:
        rest = notation[1:]
        parts = re.findall(r"[0-9][mps]", rest)
        if len(parts) == 2:
            a, b = int(parts[0][0]), int(parts[1][0])
            if abs(a - b) == 1:
                consumed_num = min(a, b) - 1 if min(a, b) > 1 else max(a, b) + 1
                suit = parts[0][1]
                consumed = f"{consumed_num}{suit}" if 1 <= consumed_num <= 9 else f"4{suit}"
                return (consumed, parts[0], parts[1])
    return None


def parse_pon_notation(notation: str) -> Optional[List[str]]:
    """解析碰的符号，如 p1z1z，返回三张相同牌。"""
    notation = notation.strip().lower()
    if not notation.startswith("p") or len(notation) < 4:
        return None
    rest = notation[1:]
    parts = re.findall(r"[0-9][mpsz]", rest)
    if len(parts) >= 2:
        return [parts[0], parts[0], parts[0]]
    return None


def _get_tile_path(tile_str: str) -> Optional[Tuple[str, Path]]:
    """获取牌面文件路径。PNG 优先（预渲染立体、不透明），缺失时用 SVG。"""
    fname = TILE_TO_FILENAME.get(tile_str) or CN_TO_FILENAME.get(tile_str)
    if not fname:
        return None
    png_path = _tiles_png_dir() / f"{fname}.png"
    if png_path.exists():
        return ("png", png_path)
    svg_path = _tiles_svg_dir() / f"{fname}.svg"
    if svg_path.exists():
        return ("svg", svg_path)
    return None


def _get_glyph_path(tile_str: str) -> Optional[Tuple[str, Path]]:
    """Get glyph-only image path (transparent symbol) if provided by user."""
    fname = TILE_TO_FILENAME.get(tile_str) or CN_TO_FILENAME.get(tile_str)
    if not fname:
        return None
    for d in _glyph_search_dirs():
        png_path = d / f"{fname}.png"
        if png_path.exists():
            return ("png", png_path)
        svg_path = d / f"{fname}.svg"
        if svg_path.exists():
            return ("svg", svg_path)
    return None


def _get_regular_svg_path(tile_str: str) -> Optional[Path]:
    """Get FluffyStuff Regular SVG path for a tile symbol."""
    fname = TILE_TO_FILENAME.get(tile_str) or CN_TO_FILENAME.get(tile_str)
    if not fname:
        return None
    p = _tiles_svg_dir() / f"{fname}.svg"
    return p if p.exists() else None


def render_illustration_to_qimage(
    notation: str,
    tile_width: int = 56,
    tile_height: int = 75,
    scale: float = 3.0,
    bg_color: Tuple[int, int, int] = (220, 235, 255),
) -> "QImage":
    """
    将符号渲染为 QImage。
    优先使用 riichi-mahjong-tiles 的 PNG，缺失时回退到手绘。
    """
    w = int((tile_width * 4 + 80) * scale)
    h = int((tile_height * 2 + 100) * scale)
    is_big_q = notation.strip() == "?"
    img = QImage(w, h, QImage.Format_ARGB32 if is_big_q else QImage.Format_RGB32)
    img.fill(Qt.transparent if is_big_q else QColor(*bg_color))

    painter = QPainter(img)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setRenderHint(QPainter.SmoothPixmapTransform)
    painter.setRenderHint(QPainter.TextAntialiasing)

    try:
        if not is_big_q:
            _draw_table_background(painter, w, h, bg_color)
        chi_data = parse_chi_notation(notation)
        pon_data = parse_pon_notation(notation)
        if chi_data:
            _draw_chi_meld(painter, chi_data, tile_width, tile_height, scale, w, h)
        elif pon_data:
            _draw_pon_meld(painter, pon_data, tile_width, tile_height, scale, w, h)
        else:
            # 单独输入 ?（无 -）：输出一个更大的问号，无色背景便于复制到作图软件
            if is_big_q:
                _draw_big_question_mark(painter, w, h, scale)
            else:
                # 优先按 - 分割，以支持 7s-?-9s 中的 ?（问号牌）
                _draw_tiles_or_placeholder(painter, notation, tile_width, tile_height, scale, w, h)

        # 右下角署名（单独问号时不显示，保持透明背景洁净）
        if not is_big_q:
            _draw_credit(painter, w, h, scale)
    finally:
        painter.end()

    return img


def _draw_big_question_mark(painter: "QPainter", img_w: int, img_h: int, scale: float) -> None:
    """单独输入 ? 时，在画布中央绘制一个更大的问号。"""
    sz = int(min(img_w, img_h) * 0.35)
    font = QFont("Arial", sz)
    font.setWeight(87)
    painter.setFont(font)
    painter.setPen(QColor(40, 40, 55))
    rect = QRectF(0, 0, img_w, img_h)
    painter.drawText(rect, Qt.AlignCenter, "?")


def _draw_tiles_or_placeholder(
    painter: "QPainter",
    notation: str,
    tile_width: int, tile_height: int,
    scale: float,
    img_w: int, img_h: int,
) -> None:
    """按 - 或 AND 分割解析牌序列并绘制，或显示占位提示。"""
    parts = split_discard_pattern(notation)
    if len(parts) > 1:
        tiles = []
        for p in parts:
            if p in "?$*":
                tiles.append("?")
            elif "NOT" in p.upper() or "OR" in p.upper() or re.match(r"^\[\d\d\][mpsz]$", p, re.I):
                tiles.append("?")  # NOT/OR/[xy] 逻辑符在示意图中用 ? 表示
            elif p in ("m", "p", "s"):
                tiles.append("?")  # 花色通配符 任意万/饼/索
            elif re.match(r"^[Nn][Oo][Tt][mps]$", p):
                tiles.append("?")  # NOTm/NOTp/NOTs 任意一张非万/饼/索
            elif re.fullmatch(r"[0-9][mps]|\d?z|[东南西北白发中]", p):
                tiles.append(p)
            elif len(p) >= 2:
                tiles.append(p)
    else:
        tiles = re.findall(r"[0-9][mps]|\d?z|[东南西北白发中]", notation.replace(" ", ""))
    if not tiles and parts:
        tiles = [p if p in "?$*" or len(p) >= 2 else "?" for p in parts if p]
        tiles = ["?" if t in ("$", "*") else t for t in tiles]
    if tiles:
        _draw_tile_row(painter, tiles, tile_width, tile_height, scale, img_w, img_h)
    else:
        _draw_placeholder(painter, notation, img_w, img_h)


def _draw_credit(painter: "QPainter", img_w: int, img_h: int, scale: float) -> None:
    """在右下角绘制 FluffyStuff 署名，小字号。"""
    painter.setPen(QColor(100, 110, 130))
    painter.setFont(QFont("Microsoft YaHei", max(6, int(7 * scale))))
    rect = QRectF(0, img_h - 16 * scale, img_w - 6, 14 * scale)
    painter.drawText(rect, Qt.AlignRight | Qt.AlignBottom, CREDIT_TEXT)


def _draw_single_tile(
    painter: "QPainter",
    tile_str: str,
    x: float, y: float,
    width: float, height: float,
    horizontal: bool = False,
) -> None:
    """绘制单张牌：空白牌体 + 字符叠加（字符来自 repo PNG 或手绘）。"""
    _draw_tile_composite(painter, tile_str, x, y, width, height, horizontal)


def _draw_tile_composite(
    painter: "QPainter",
    tile_str: str,
    x: float, y: float,
    width: float, height: float,
    horizontal: bool,
) -> None:
    """Draw tile: 优先 3d_tile.glb 渲染，其次预烘焙图，否则 2D 斜角绘制。"""
    w = height if horizontal else width
    h = width if horizontal else height
    iw, ih = int(w), int(h)

    # 竖牌：优先用 3d_tile.glb 实时渲染（字符贴到牌面）
    if not horizontal:
        cache_key = (tile_str, iw, ih)
        if cache_key not in _TILE_3D_CACHE:
            try:
                from .tile_3d_renderer import render_tile_3d
                char_img = _load_character_image(tile_str) if tile_str not in ("_", "?") else None
                text = _tile_display_text(tile_str) if tile_str == "?" else None  # ? 用手绘
                arr = render_tile_3d(tile_str, (iw, ih), char_img=char_img, text=text)
                if arr is not None and arr.size > 0:
                    import numpy as np
                    h_arr, w_arr = arr.shape[:2]
                    if arr.ndim == 3 and arr.shape[2] >= 3:
                        rgb = np.ascontiguousarray(arr[:, :, :3])
                        qimg = QImage(rgb.data, w_arr, h_arr, w_arr * 3, QImage.Format_RGB888)
                    else:
                        qimg = QImage(w_arr, h_arr, QImage.Format_RGB32)
                        qimg.fill(QColor(253, 250, 242))
                    if not qimg.isNull():
                        _TILE_3D_CACHE[cache_key] = qimg.copy()
            except Exception:
                pass
        if cache_key in _TILE_3D_CACHE:
            painter.drawImage(QRectF(x, y, w, h), _TILE_3D_CACHE[cache_key], QRectF(0, 0, iw, ih))
            return

    # 竖牌回退：预烘焙 3D 图（bake_3d_tiles.py 生成，含完整字符）
    if not horizontal:
        baked = _load_baked_3d_tile(tile_str, iw, ih)
        if baked is not None and not baked.isNull():
            painter.drawImage(QRectF(x, y, w, h), baked, QRectF(0, 0, baked.width(), baked.height()))
            return

    # Depth vector points to upper-right, so top and side share the same slope.
    depth_x = max(4.0, min(w, h) * 0.16)
    depth_y = max(3.0, min(w, h) * 0.12)
    rx, ry = max(3.0, w * 0.08), max(3.0, h * 0.06)
    seam_overlap = max(0.8, min(w, h) * 0.012)

    # Keep final bounds as (x, y, w, h) by placing front face lower.
    fx = x
    fy = y + depth_y
    fw = max(1.0, w - depth_x)
    fh = max(1.0, h - depth_y)
    seam_x = fx + fw

    # Soft shadow on table.
    shadow_rect = QRectF(fx + depth_x * 0.7, fy + 2.0, fw, fh)
    sh_grad = QLinearGradient(shadow_rect.left(), shadow_rect.top(), shadow_rect.right(), shadow_rect.bottom())
    sh_grad.setColorAt(0.0, QColor(0, 0, 0, 70))
    sh_grad.setColorAt(1.0, QColor(0, 0, 0, 8))
    painter.setBrush(QBrush(sh_grad))
    painter.setPen(Qt.NoPen)
    painter.drawRoundedRect(shadow_rect, rx, ry)

    top_poly = QPolygonF([
        QPointF(fx, fy),
        QPointF(seam_x, fy),
        QPointF(fx + fw + depth_x, fy - depth_y),
        QPointF(fx + depth_x, fy - depth_y),
    ])
    side_poly = QPolygonF([
        QPointF(seam_x - seam_overlap, fy),
        QPointF(fx + fw + depth_x, fy - depth_y),
        QPointF(fx + fw + depth_x, fy + fh - depth_y),
        QPointF(seam_x - seam_overlap, fy + fh),
    ])

    painter.setPen(QPen(QColor(186, 180, 166), 1))
    painter.setBrush(QBrush(QColor(244, 239, 229)))
    painter.drawPolygon(top_poly)
    painter.setBrush(QBrush(QColor(225, 217, 202)))
    painter.drawPolygon(side_poly)

    blank = _create_blank_tile(int(fw), int(fh))
    painter.drawImage(QRectF(fx, fy, fw, fh), blank, QRectF(0, 0, int(fw), int(fh)))

    # Right half-tone band on the front face (your black-marked region).
    front_rect = QRectF(fx, fy, fw, fh)
    front_path = QPainterPath()
    front_path.addRoundedRect(front_rect, rx, ry)
    band_w = max(2.0, fw * 0.22)
    band_rect = QRectF(seam_x - band_w, fy + 0.6, band_w, max(1.0, fh - 1.2))
    band_grad = QLinearGradient(band_rect.left(), band_rect.top(), band_rect.right(), band_rect.bottom())
    band_grad.setColorAt(0.0, QColor(235, 228, 214, 115))
    band_grad.setColorAt(0.55, QColor(220, 210, 192, 145))
    band_grad.setColorAt(1.0, QColor(205, 194, 175, 170))
    painter.save()
    painter.setClipPath(front_path)
    painter.fillRect(band_rect, QBrush(band_grad))
    painter.restore()

    # Seam line to fully eliminate anti-alias gap at A.
    painter.setPen(QPen(QColor(172, 164, 150, 110), 1))
    painter.drawLine(QPointF(seam_x - 0.5, fy + 1), QPointF(seam_x - 0.5, fy + fh - 1))

    # Round off sharp corner B with a blended cap.
    cx = fx + fw + depth_x
    cy = fy - depth_y
    cr = max(1.8, min(depth_x, depth_y) * 0.62)
    corner_grad = QLinearGradient(cx - cr, cy - cr, cx + cr, cy + cr)
    corner_grad.setColorAt(0.0, QColor(244, 239, 229))
    corner_grad.setColorAt(1.0, QColor(225, 217, 202))
    painter.setPen(QPen(QColor(186, 180, 166), 1))
    painter.setBrush(QBrush(corner_grad))
    painter.drawEllipse(QRectF(cx - cr, cy - cr, cr * 2, cr * 2))

    # Accent line on the top front edge.
    painter.setPen(QPen(QColor(196, 78, 74), max(1, int(min(fw, fh) * 0.028))))
    painter.drawLine(QPointF(fx + 3, fy + 1), QPointF(fx + fw - 3, fy + 1))

    if tile_str == "?":
        _draw_question_mark_tile(painter, fx, fy, fw, fh, horizontal)
        return
    char_img = _load_character_image(tile_str)
    if char_img and not char_img.isNull():
        _overlay_character(painter, char_img, fx, fy, fw, fh, horizontal)
    else:
        _draw_character_text(painter, tile_str, fx, fy, fw, fh, horizontal)


def _load_character_image(tile_str: str) -> Optional["QImage"]:
    """加载字符层贴图：优先 glyph 素材，回退到旧整张牌图自动抠字。"""
    cached = _CHAR_IMG_CACHE.get(tile_str)
    if cached is not None:
        return cached.copy()

    def _load_png(path: Path) -> Optional["QImage"]:
        img = QImage(str(path.resolve()))
        if img.isNull():
            return None
        if img.format() != QImage.Format_ARGB32:
            img = img.convertToFormat(QImage.Format_ARGB32)
        return img

    def _render_svg(path: Path, size: int = 512) -> Optional["QImage"]:
        # Prefer cairosvg to avoid black background artifacts in some Qt SVG renders.
        try:
            import cairosvg
            import io
            with open(path, "rb") as f:
                svg_data = f.read()
            buf = io.BytesIO()
            cairosvg.svg2png(
                bytestring=svg_data,
                write_to=buf,
                output_width=size,
                output_height=size,
                background_color="white",
            )
            img = QImage()
            if img.loadFromData(buf.getvalue(), "PNG") and not img.isNull():
                if img.format() != QImage.Format_ARGB32:
                    img = img.convertToFormat(QImage.Format_ARGB32)
                return img
        except Exception:
            pass
        try:
            from PyQt5.QtSvg import QSvgRenderer
            renderer = QSvgRenderer(str(path.resolve()))
            if not renderer.isValid():
                return None
            img = QImage(size, size, QImage.Format_ARGB32)
            img.fill(Qt.transparent)
            p = QPainter(img)
            p.setRenderHint(QPainter.Antialiasing)
            p.setRenderHint(QPainter.SmoothPixmapTransform)
            renderer.render(p, QRectF(0, 0, size, size))
            p.end()
            return img
        except Exception:
            return None

    def _extract_symbol_from_full_tile(img: "QImage") -> "QImage":
        """从旧整张牌图中去掉白色牌底，保留字符。"""
        src = img if img.format() == QImage.Format_ARGB32 else img.convertToFormat(QImage.Format_ARGB32)
        out = QImage(src.width(), src.height(), QImage.Format_ARGB32)
        out.fill(Qt.transparent)
        margin_x = max(1, int(src.width() * 0.12))
        margin_y = max(1, int(src.height() * 0.10))
        for y in range(src.height()):
            for x in range(src.width()):
                # Drop tile border area from legacy full-tile art.
                if x < margin_x or x >= src.width() - margin_x or y < margin_y or y >= src.height() - margin_y:
                    continue
                c = QColor(src.pixel(x, y))
                if c.alpha() < 8:
                    continue
                r, g, b = c.red(), c.green(), c.blue()
                vmax = max(r, g, b)
                vmin = min(r, g, b)
                sat = vmax - vmin
                # Keep dark strokes and colored symbols; discard bright low-sat tile body.
                keep = (vmax < 195) or (sat > 16 and vmax < 250)
                if keep:
                    out.setPixelColor(x, y, QColor(r, g, b, c.alpha()))
        return out

    # 1) 优先读取用户提供的 glyph-only 素材（透明底）
    glyph_res = _get_glyph_path(tile_str)
    if glyph_res:
        gtype, gpath = glyph_res
        glyph_img = _load_png(gpath) if gtype == "png" else _render_svg(gpath)
        if glyph_img and not glyph_img.isNull():
            _CHAR_IMG_CACHE[tile_str] = glyph_img.copy()
            return glyph_img

    # 2) Prefer FluffyStuff Regular SVG -> render -> extract symbol.
    regular_svg = _get_regular_svg_path(tile_str)
    if regular_svg is not None:
        base_svg = _render_svg(regular_svg, size=768)
        if base_svg and not base_svg.isNull():
            symbol = _extract_symbol_from_full_tile(base_svg)
            _CHAR_IMG_CACHE[tile_str] = symbol.copy()
            return symbol

    # 3) 回退：旧整张牌图 -> 自动抠字
    legacy_res = _get_tile_path(tile_str)
    if not legacy_res:
        return None
    ltype, lpath = legacy_res
    if ltype == "png":
        base_img = _load_png(lpath)
    else:
        png_path = _tiles_png_dir() / f"{lpath.stem}.png"
        base_img = _load_png(png_path) if png_path.exists() else _render_svg(lpath)
    if not base_img or base_img.isNull():
        return None

    symbol = _extract_symbol_from_full_tile(base_img)
    _CHAR_IMG_CACHE[tile_str] = symbol.copy()
    return symbol


def _overlay_character(
    painter: "QPainter",
    char_img: "QImage",
    x: float, y: float,
    w: float, h: float,
    horizontal: bool,
) -> None:
    """将字符图叠加到牌面中心，保持长宽比。"""
    pix = QPixmap.fromImage(char_img)
    if horizontal:
        pix = pix.transformed(QTransform().rotate(-90))
    scaled = pix.scaled(int(w * 0.58), int(h * 0.58), Qt.KeepAspectRatio, Qt.SmoothTransformation)
    sx = x + (w - scaled.width()) / 2
    sy = y + (h - scaled.height()) / 2
    painter.drawPixmap(int(sx), int(sy), scaled)


def _draw_question_mark_tile(
    painter: "QPainter",
    x: float, y: float,
    w: float, h: float,
    horizontal: bool,
) -> None:
    """绘制问号牌：粗体醒目的 ?，表示未知/任意牌。"""
    sz = int(min(w, h) * 0.45)
    font = QFont("Arial", sz)
    font.setWeight(87)  # QFont.Black，最粗字重，更醒目
    painter.setFont(font)
    painter.setPen(QColor(40, 40, 55))
    rect = QRectF(x, y, w, h)
    painter.drawText(rect, Qt.AlignCenter, "?")


def _draw_character_text(
    painter: "QPainter",
    tile_str: str,
    x: float, y: float,
    w: float, h: float,
    horizontal: bool,
) -> None:
    """手绘字符（repo 缺失时）。"""
    painter.setPen(QColor(40, 35, 30))
    sz = int(min(w, h) * 0.38)
    painter.setFont(QFont("Microsoft YaHei", sz))
    text = _tile_display_text(tile_str)
    painter.drawText(QRectF(x, y, w, h), Qt.AlignCenter, text)


def _draw_tile_svg(
    painter: "QPainter",
    svg_path: str,
    x: float, y: float,
    width: float, height: float,
    horizontal: bool,
) -> bool:
    """渲染 SVG。优先用 cairosvg（立体完整），否则 Qt QSvgRenderer。"""
    w = height if horizontal else width
    h = width if horizontal else height
    rect = QRectF(x, y, w, h)
    painter.fillRect(rect, QColor(255, 255, 255))
    rw, rh = int(w), int(h)
    if horizontal:
        rw, rh = rh, rw
    try:
        import cairosvg
        import io
        with open(svg_path, "rb") as f:
            svg_data = f.read()
        buf = io.BytesIO()
        cairosvg.svg2png(
            bytestring=svg_data, write_to=buf,
            output_width=rw, output_height=rh,
            background_color="white",
        )
        buf.seek(0)
        img = QImage()
        if img.loadFromData(buf.getvalue(), "PNG") and not img.isNull():
            if horizontal:
                img = img.transformed(QTransform().rotate(-90))
            painter.drawImage(rect, img)
            return True
    except TypeError:
        try:
            import cairosvg
            import io
            with open(svg_path, "rb") as f:
                svg_data = f.read()
            buf = io.BytesIO()
            cairosvg.svg2png(bytestring=svg_data, write_to=buf, output_width=rw, output_height=rh)
            buf.seek(0)
            img = QImage()
            if img.loadFromData(buf.getvalue(), "PNG") and not img.isNull():
                if horizontal:
                    img = img.transformed(QTransform().rotate(-90))
                painter.drawImage(rect, img)
                return True
        except Exception:
            pass
    except Exception:
        pass
    # 回退到 Qt QSvgRenderer
    try:
        from PyQt5.QtSvg import QSvgRenderer
        renderer = QSvgRenderer(svg_path)
        if renderer.isValid():
            if horizontal:
                painter.save()
                painter.translate(x + w / 2, y + h / 2)
                painter.rotate(-90)
                painter.translate(-h / 2, -w / 2)
                renderer.render(painter, QRectF(0, 0, h, w))
                painter.restore()
            else:
                renderer.render(painter, QRectF(x, y, w, h))
            return True
    except Exception:
        pass
    return False


def _draw_tile_pixmap(
    painter: "QPainter",
    tile_img: "QImage",
    x: float, y: float,
    width: float, height: float,
    horizontal: bool,
) -> None:
    """将牌面图片缩放绘制到指定矩形。填满槽、高分辨率，保留立体细节。"""
    w = height if horizontal else width
    h = width if horizontal else height
    rect = QRectF(x, y, w, h)
    painter.fillRect(rect, QColor(255, 255, 255))
    if tile_img.format() != QImage.Format_ARGB32 and tile_img.hasAlphaChannel():
        tile_img = tile_img.convertToFormat(QImage.Format_ARGB32)
    pix = QPixmap.fromImage(tile_img)
    if horizontal:
        pix = pix.transformed(QTransform().rotate(-90))
    scaled = pix.scaled(int(w), int(h), Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    painter.drawPixmap(int(x), int(y), scaled)


def _draw_tile_fallback(
    painter: "QPainter",
    tile_str: str,
    x: float, y: float,
    width: float, height: float,
    horizontal: bool,
) -> None:
    """手绘牌面（PNG 缺失时回退）。"""
    w = height if horizontal else width
    h = width if horizontal else height
    grad = QLinearGradient(x, y, x + w, y + h)
    grad.setColorAt(0, QColor(255, 252, 245))
    grad.setColorAt(0.5, QColor(255, 250, 235))
    grad.setColorAt(1, QColor(245, 240, 225))
    painter.setBrush(QBrush(grad))
    painter.setPen(QPen(QColor(180, 170, 155), 1))
    painter.drawRoundedRect(QRectF(x, y, w, h), 4, 4)
    painter.setPen(QColor(40, 35, 30))
    painter.setFont(QFont("Microsoft YaHei", int(min(w, h) * 0.3)))
    text = _tile_display_text(tile_str)
    painter.drawText(QRectF(x, y, w, h), Qt.AlignCenter, text)


def _tile_display_text(tile_str: str) -> str:
    """牌面显示文字（手绘回退用）。"""
    MANZU = ["一", "二", "三", "四", "五", "六", "七", "八", "九"]
    HONOR = {"1z": "东", "2z": "南", "3z": "西", "4z": "北", "5z": "白", "6z": "發", "7z": "中"}
    if tile_str in HONOR:
        return HONOR[tile_str]
    if tile_str in CN_TO_FILENAME:
        return tile_str  # 已是中文
    if len(tile_str) == 2 and tile_str[0].isdigit() and tile_str[1] in "mps":
        n = int(tile_str[0])
        if tile_str[1] == "m":
            return MANZU[n - 1] if n else "五"
        return str(n or 5)
    return tile_str


def _draw_chi_meld(
    painter: "QPainter",
    chi_data: Tuple[str, str, str],
    tw: int, th: int,
    scale: float,
    img_w: int, img_h: int,
) -> None:
    """绘制吃牌副露：被吃的牌（4m）在最左横放，手牌（3m5m）在右竖放。无间隙、底部对齐。"""
    consumed, left, right = chi_data
    sw, sh = tw * scale, th * scale
    total_w = sh + sw + sw
    base_x = (img_w - total_w) / 2
    base_y = (img_h - sh * 2) / 2 - 30 * scale

    # 4m 被吃的牌：最左、横放，底部与竖牌对齐
    mid_y = base_y + sh - sw
    _draw_single_tile(painter, consumed, base_x, mid_y, sw, sh, True)
    # 3m 5m 手牌：右侧、竖放，无间隙
    left_x = base_x + sh
    _draw_single_tile(painter, left, left_x, base_y, sw, sh, False)
    right_x = base_x + sh + sw
    _draw_single_tile(painter, right, right_x, base_y, sw, sh, False)

    # 红色箭头：从手牌指向被吃的牌（无间隙时缩短）
    ax1 = base_x + sh + 4
    ax2 = base_x + sh - 6
    ay = base_y + sh / 2
    painter.setPen(QPen(QColor(220, 60, 60), max(2, int(2 * scale))))
    painter.setBrush(QBrush(QColor(220, 60, 60)))
    painter.drawLine(int(ax1), int(ay), int(ax2), int(ay))
    head = QPolygonF([QPointF(ax2, ay), QPointF(ax2 - 8, ay - 5), QPointF(ax2 - 8, ay + 5)])
    painter.drawPolygon(head)


def _draw_pon_meld(
    painter: "QPainter",
    tiles: List[str],
    tw: int, th: int,
    scale: float,
    img_w: int, img_h: int,
) -> None:
    """绘制碰牌副露。无间隙、横牌底部与竖牌对齐。"""
    sw, sh = tw * scale, th * scale
    total_w = sw * 2 + sh
    base_x = (img_w - total_w) / 2
    base_y = (img_h - sh * 2) / 2 - 30 * scale
    _draw_single_tile(painter, tiles[0], base_x, base_y, sw, sh, False)
    _draw_single_tile(painter, tiles[1], base_x + sw, base_y, sw, sh, False)
    mid_x = base_x + sw * 2
    mid_y = base_y + sh - sw  # 底部对齐
    _draw_single_tile(painter, tiles[2], mid_x, mid_y, sw, sh, True)


def _draw_tile_row(
    painter: "QPainter",
    tiles: List[str],
    tw: int, th: int,
    scale: float,
    img_w: int, img_h: int,
) -> None:
    """绘制一排牌。"""
    pad = 1.4 * scale
    sw, sh = tw * scale, th * scale
    total_w = len(tiles) * sw + (len(tiles) - 1) * pad
    base_x = (img_w - total_w) / 2
    base_y = (img_h - sh) / 2 - 16 * scale
    for i, t in enumerate(tiles):
        x = base_x + i * (sw + pad)
        _draw_single_tile(painter, t.strip(), x, base_y, sw, sh, False)


def _draw_placeholder(painter: "QPainter", text: str, w: int, h: int) -> None:
    """无法解析时显示占位。"""
    painter.setPen(QColor(120, 120, 130))
    painter.setFont(QFont("Microsoft YaHei", 14))
    painter.drawText(QRectF(0, 0, w, h), Qt.AlignCenter, f"无法解析: {text}\n支持如 4mc3m5m、p1z1z")


def generate_illustration(
    notation: str,
    output_path: str,
    tile_width: int = 44,
    tile_height: int = 58,
    scale: float = 2.0,
    bg_color: Tuple[int, int, int] = (220, 235, 255),
) -> bool:
    """生成示意图并保存到文件。"""
    try:
        from PyQt5.QtWidgets import QApplication
        app = QApplication.instance()
        if app is None:
            app = QApplication([])
    except Exception:
        return False
    img = render_illustration_to_qimage(notation, tile_width, tile_height, scale, bg_color)
    return img.save(output_path)
