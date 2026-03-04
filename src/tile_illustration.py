"""
麻将牌示意图生成器

根据舍牌/副露符号（如 4mc3m5m）生成示意图图片，用于展示分析场景。
直接使用 assets/tile-assets 中的完整 3D 牌面素材，不进行任何额外绘制。
"""

import re
from pathlib import Path
from typing import List, Tuple, Optional, Dict

from .equivalent_variants import split_discard_pattern

# PyQt5 统一导入，避免各函数内遗漏
from PyQt5.QtCore import Qt, QPointF, QRectF, QRect
from PyQt5.QtGui import (
    QImage, QPainter, QColor, QPen, QBrush, QFont,
    QLinearGradient, QPixmap, QTransform, QPolygonF,
)

# 完整牌面图缓存（避免重复加载）
_TILE_IMG_CACHE: Dict[str, QImage] = {}
_TILE_FLAT_CACHE: Dict[str, QImage] = {}

# 麻将桌背景图（项目内，缺失时回退到渐变绘制）
_TABLE_BG_PATH = Path(__file__).parent.parent / "assets" / "tile-assets" / "mahjong_table.png"
_TABLE_BG_CACHE: Optional["QImage"] = None


def _draw_table_background(
    painter: "QPainter",
    width: int,
    height: int,
    bg_color: Tuple[int, int, int],
) -> None:
    """绘制背景：优先使用 mahjong_table.png，缺失时用渐变绘制。"""
    global _TABLE_BG_CACHE
    if _TABLE_BG_PATH.exists():
        if _TABLE_BG_CACHE is None:
            _TABLE_BG_CACHE = QImage(str(_TABLE_BG_PATH.resolve()))
        if not _TABLE_BG_CACHE.isNull():
            scaled = _TABLE_BG_CACHE.scaled(width, height, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            painter.drawImage(QRectF(0, 0, width, height), scaled, QRectF(0, 0, scaled.width(), scaled.height()))
            return

    # 回退：渐变绘制
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


# 牌面图片映射：notation -> 文件名 (不含扩展名，与 assets/tile-assets 一致)
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
TILE_TO_FILENAME["b"] = "tile_back"  # 牌背，输入 b 显示
# 中文 -> 文件名（直接查图用）
CN_TO_FILENAME = {"东": "Ton", "南": "Nan", "西": "Shaa", "北": "Pei", "白": "Haku", "發": "Hatsu", "发": "Hatsu", "中": "Chun"}

# 牌面资源目录：使用 assets/tile-assets（正放牌），SVG 优先
def _tiles_svg_dir() -> Path:
    return Path(__file__).parent.parent / "assets" / "tile-assets" / "Regular"

def _tiles_png_dir() -> Path:
    return Path(__file__).parent.parent / "assets" / "tile-assets" / "Export" / "Regular"


def _tiles_flat_dir() -> Path:
    """桌上视角（平放）牌素材。"""
    return Path(__file__).parent.parent / "assets" / "tile-assets" / "flat"


CREDIT_TEXT = "Tiles: custom assets"


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
    """获取牌面文件路径。PNG 优先，b=牌背(tile_back.png)。"""
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


def render_illustration_to_qimage(
    notation: str,
    tile_width: int = 56,
    tile_height: int = 75,
    scale: float = 3.0,
    bg_color: Tuple[int, int, int] = (220, 235, 255),
    discard_notation: Optional[str] = None,
) -> "QImage":
    """
    将符号渲染为 QImage。
    notation: 主排（正放牌，支持吃/碰/舍牌序列），可为空（仅舍牌时）
    discard_notation: 舍牌排（桌上视角牌，可选）
    """
    discard_notation = (discard_notation or "").strip()
    has_discard = bool(discard_notation)
    has_main = bool(notation and notation.strip())
    row_gap = int(40 * scale) if (has_main and has_discard) else 0
    w = int((tile_width * 4 + 80) * scale)
    h = int((tile_height * 2 + 100) * scale) + row_gap

    is_big_q = notation.strip() == "?" and not has_discard
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

        if has_discard:
            if has_main:
                # 主排在偏上，舍牌排在偏下
                main_h = h - row_gap
                if chi_data:
                    _draw_chi_meld(painter, chi_data, tile_width, tile_height, scale, w, main_h)
                elif pon_data:
                    _draw_pon_meld(painter, pon_data, tile_width, tile_height, scale, w, main_h)
                else:
                    if notation.strip() == "?":
                        _draw_big_question_mark(painter, w, main_h, scale)
                    else:
                        _draw_tiles_or_placeholder(
                            painter, notation, tile_width, tile_height, scale, w, main_h, use_flat=False
                        )
                sw, sh = tile_width * scale, tile_height * scale
                flat_base_y = main_h + (row_gap - sh) / 2 - 30 * scale  # 舍牌排上移
                _draw_tiles_or_placeholder(
                    painter, discard_notation, tile_width, tile_height, scale, w, h,
                    use_flat=True, flat_base_y=flat_base_y,
                )
            else:
                # 仅舍牌排，占主区域
                _draw_tiles_or_placeholder(
                    painter, discard_notation, tile_width, tile_height, scale, w, h,
                    use_flat=True,
                )
        else:
            if chi_data:
                _draw_chi_meld(painter, chi_data, tile_width, tile_height, scale, w, h)
            elif pon_data:
                _draw_pon_meld(painter, pon_data, tile_width, tile_height, scale, w, h)
            else:
                if is_big_q:
                    _draw_big_question_mark(painter, w, h, scale)
                else:
                    _draw_tiles_or_placeholder(
                        painter, notation, tile_width, tile_height, scale, w, h, use_flat=False
                    )

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


def _parse_notation_to_tiles(notation: str) -> Optional[List[str]]:
    """解析符号为牌列表。解析失败返回 None。"""
    if not notation or not notation.strip():
        return None
    parts = split_discard_pattern(notation)
    if len(parts) > 1:
        tiles = []
        for p in parts:
            if p in "?$*":
                tiles.append("?")
            elif p.lower() == "b":
                tiles.append("b")
            elif "NOT" in p.upper() or "OR" in p.upper() or re.match(r"^\[\d\d\][mpsz]$", p, re.I):
                tiles.append("?")
            elif p in ("m", "p", "s"):
                tiles.append("?")
            elif re.match(r"^[Nn][Oo][Tt][mps]$", p):
                tiles.append("?")
            elif re.fullmatch(r"[0-9][mps]|\d?z|[东南西北白发中]", p):
                tiles.append(p)
            elif len(p) >= 2:
                tiles.append(p)
    else:
        tiles = re.findall(r"[0-9][mps]|\d?z|[东南西北白发中]", notation.replace(" ", ""))
    if not tiles and parts:
        tiles = [p if p in "?$*" or len(p) >= 2 or p.lower() == "b" else "?" for p in parts if p]
        tiles = ["?" if t in ("$", "*") else t for t in tiles]
    return tiles if tiles else None


def _draw_tiles_or_placeholder(
    painter: "QPainter",
    notation: str,
    tile_width: int, tile_height: int,
    scale: float,
    img_w: int, img_h: int,
    use_flat: bool = False,
    flat_base_y: Optional[float] = None,
) -> None:
    """按 - 或 AND 分割解析牌序列并绘制，或显示占位提示。"""
    tiles = _parse_notation_to_tiles(notation)
    if tiles:
        if use_flat:
            _draw_tile_row_flat(
                painter, tiles, tile_width, tile_height, scale, img_w, img_h, flat_base_y
            )
        else:
            _draw_tile_row(painter, tiles, tile_width, tile_height, scale, img_w, img_h)
    else:
        _draw_placeholder(painter, notation, img_w, img_h)


def _draw_credit(painter: "QPainter", img_w: int, img_h: int, scale: float) -> None:
    """在右下角绘制素材署名，小字号。"""
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
    use_flat: bool = False,
) -> None:
    """绘制单张牌：直接贴完整素材。use_flat=True 时用桌上视角（平放）牌。"""
    w = height if horizontal else width
    h = width if horizontal else height
    rect = QRectF(x, y, w, h)

    if tile_str == "?":
        _draw_question_mark_tile(painter, x, y, w, h, horizontal)
        return

    tile_img = _load_flat_tile_image(tile_str) if use_flat else _load_full_tile_image(tile_str)
    if tile_img and not tile_img.isNull():
        pix = QPixmap.fromImage(tile_img)
        if use_flat:
            pix = pix.transformed(QTransform().rotate(180))
        elif horizontal:
            pix = pix.transformed(QTransform().rotate(-90))
        scaled = pix.scaled(int(w), int(h), Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        painter.drawPixmap(rect.toRect(), scaled)
    else:
        _draw_character_text(painter, tile_str, x, y, w, h, horizontal)


def _load_full_tile_image(tile_str: str, size: int = 512) -> Optional["QImage"]:
    """加载完整牌面图（PNG 或 SVG 渲染）。"""
    cached = _TILE_IMG_CACHE.get(tile_str)
    if cached is not None:
        return cached.copy()

    def _load_png(path: Path) -> Optional["QImage"]:
        img = QImage(str(path.resolve()))
        if img.isNull():
            return None
        if img.format() != QImage.Format_ARGB32 and img.hasAlphaChannel():
            img = img.convertToFormat(QImage.Format_ARGB32)
        return img

    def _render_svg(path: Path, sz: int = 512) -> Optional["QImage"]:
        try:
            import cairosvg
            import io
            with open(path, "rb") as f:
                svg_data = f.read()
            buf = io.BytesIO()
            cairosvg.svg2png(
                bytestring=svg_data, write_to=buf,
                output_width=sz, output_height=sz,
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
            img = QImage(sz, sz, QImage.Format_ARGB32)
            img.fill(QColor(255, 255, 255))
            p = QPainter(img)
            p.setRenderHint(QPainter.Antialiasing)
            p.setRenderHint(QPainter.SmoothPixmapTransform)
            renderer.render(p, QRectF(0, 0, sz, sz))
            p.end()
            return img
        except Exception:
            return None

    res = _get_tile_path(tile_str)
    if not res:
        return None
    ltype, lpath = res
    if ltype == "png":
        img = _load_png(lpath)
    else:
        png_path = _tiles_png_dir() / f"{lpath.stem}.png"
        img = _load_png(png_path) if png_path.exists() else _render_svg(lpath, sz=size)
    if img and not img.isNull():
        _TILE_IMG_CACHE[tile_str] = img.copy()
        return img
    return None


def _load_flat_tile_image(tile_str: str, size: int = 512) -> Optional["QImage"]:
    """加载桌上视角（平放）牌面图。"""
    cached = _TILE_FLAT_CACHE.get(tile_str)
    if cached is not None:
        return cached.copy()

    fname = TILE_TO_FILENAME.get(tile_str) or CN_TO_FILENAME.get(tile_str)
    if not fname:
        return None

    flat_path = _tiles_flat_dir() / f"{fname}_flat.png"
    if not flat_path.exists():
        return _load_full_tile_image(tile_str, size)  # 回退到正放牌

    img = QImage(str(flat_path.resolve()))
    if img.isNull():
        return _load_full_tile_image(tile_str, size)
    if img.format() != QImage.Format_ARGB32 and img.hasAlphaChannel():
        img = img.convertToFormat(QImage.Format_ARGB32)
    _TILE_FLAT_CACHE[tile_str] = img.copy()
    return img


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
    """绘制一排牌（正放）。"""
    PAD_MULT = -1
    OVERLAP_AMP = 5
    pad = PAD_MULT * scale * OVERLAP_AMP
    sw, sh = tw * scale, th * scale
    total_w = len(tiles) * sw + (len(tiles) - 1) * pad
    base_x = (img_w - total_w) / 2
    base_y = (img_h - sh) / 2 - 16 * scale
    for i, t in enumerate(tiles):
        x = base_x + i * (sw + pad)
        _draw_single_tile(painter, t.strip(), x, base_y, sw, sh, False, use_flat=False)


def _draw_tile_row_flat(
    painter: "QPainter",
    tiles: List[str],
    tw: int, th: int,
    scale: float,
    img_w: int, img_h: int,
    base_y_override: Optional[float] = None,
) -> None:
    """绘制一排牌（桌上视角/平放）。"""
    PAD_MULT = -1
    OVERLAP_AMP = 5
    pad = PAD_MULT * scale * OVERLAP_AMP
    sw, sh = tw * scale, th * scale
    total_w = len(tiles) * sw + (len(tiles) - 1) * pad
    base_x = (img_w - total_w) / 2
    base_y = base_y_override if base_y_override is not None else (img_h - sh) / 2 - 20 * scale
    for i, t in enumerate(tiles):
        x = base_x + i * (sw + pad)
        _draw_single_tile(painter, t.strip(), x, base_y, sw, sh, False, use_flat=True)


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
