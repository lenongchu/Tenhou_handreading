"""
麻将牌示意图生成器

根据舍牌/副露符号（如 4mc3m5m）生成示意图图片，用于展示分析场景。
使用自定义立体空白牌体 + FluffyStuff 字符图叠加，风格统一专业。
"""

import re
from pathlib import Path
from typing import List, Tuple, Optional, Dict

from .simple_normalizer import split_discard_pattern

# PyQt5 统一导入，避免各函数内遗漏
from PyQt5.QtCore import Qt, QPointF, QRectF, QRect
from PyQt5.QtGui import (
    QImage, QPainter, QColor, QPen, QBrush, QFont,
    QLinearGradient, QPixmap, QTransform, QPolygonF,
)

# 空白牌体缓存（避免重复生成）
_BLANK_TILE_CACHE: Dict[Tuple[int, int], QImage] = {}


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
    """
    绘制：立体空白牌体 + 字符叠加。
    字符优先从 FluffyStuff PNG 中心裁剪，缺失时手绘文字。
    """
    w = height if horizontal else width
    h = width if horizontal else height
    iw, ih = int(w), int(h)
    blank = _create_blank_tile(iw, ih)
    painter.drawImage(QRectF(x, y, w, h), blank, QRectF(0, 0, iw, ih))
    # 问号牌：粗体醒目绘制
    if tile_str == "?":
        _draw_question_mark_tile(painter, x, y, w, h, horizontal)
        return
    # 尝试叠加 repo 字符（中心裁剪），否则手绘
    char_img = _load_character_image(tile_str)
    if char_img and not char_img.isNull():
        _overlay_character(painter, char_img, x, y, w, h, horizontal)
    else:
        _draw_character_text(painter, tile_str, x, y, w, h, horizontal)


def _load_character_image(tile_str: str) -> Optional["QImage"]:
    """加载 repo 原始 PNG 完整图，不做裁剪，避免已有裁剪导致字符缺失。"""
    res = _get_tile_path(tile_str)
    if not res:
        return None
    _, path = res
    if path.suffix.lower() != ".png":
        png_path = _tiles_png_dir() / f"{path.stem}.png"
        if not png_path.exists():
            return None
        path = png_path
    img = QImage(str(path.resolve()))
    if img.isNull():
        return None
    if img.format() != QImage.Format_ARGB32 and img.hasAlphaChannel():
        img = img.convertToFormat(QImage.Format_ARGB32)
    return img


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
    scaled = pix.scaled(int(w * 0.63), int(h * 0.63), Qt.KeepAspectRatio, Qt.SmoothTransformation)
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
    pad = 4 * scale
    sw, sh = tw * scale, th * scale
    total_w = len(tiles) * sw + (len(tiles) - 1) * pad
    base_x = (img_w - total_w) / 2
    base_y = (img_h - sh) / 2 - 30 * scale
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
