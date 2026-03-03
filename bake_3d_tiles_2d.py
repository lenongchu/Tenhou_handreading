#!/usr/bin/env python3
"""
使用 2D 绘制烘焙「3D 风格」麻将牌图（无需 OpenGL/pyrender）

在 pyrender 不可用的 Windows 环境下，用 QPainter 绘制带立体感的牌面并保存。
输出到 assets/3d-tile/，示意图将自动使用。
"""
import sys
import warnings
import logging

# 抑制 OpenGL/pyglet 的已知无害警告
warnings.filterwarnings("ignore", message="Could not set COM MTA mode")
logging.getLogger("OpenGL.acceleratesupport").setLevel(logging.WARNING)

from pathlib import Path

ROOT = Path(__file__).parent
OUT_DIR = ROOT / "assets" / "3d-tile"
OUT_DIR.mkdir(parents=True, exist_ok=True)

OUT_W, OUT_H = 168, 225


def _render_tile_2d(tile_str: str, width: int, height: int) -> "object":
    """用 QPainter 绘制单张 3D 风格牌，返回 QImage。"""
    from PyQt5.QtGui import QImage, QPainter, QColor
    from PyQt5.QtCore import Qt
    sys.path.insert(0, str(ROOT))
    from src.tile_illustration import _draw_tile_composite
    # 画布略大以容纳斜角牌阴影
    pad = 20
    canvas_w = width + pad * 2
    canvas_h = height + pad * 2
    img = QImage(canvas_w, canvas_h, QImage.Format_ARGB32)
    img.fill(QColor(255, 255, 255))
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    p.setRenderHint(QPainter.TextAntialiasing)
    _draw_tile_composite(p, tile_str, pad, pad, width, height, False)
    p.end()
    # 裁剪到内容区（去除多余白边）
    return img.copy(0, 0, canvas_w, canvas_h).scaled(width, height, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)


def main():
    sys.path.insert(0, str(ROOT))
    from src.tile_illustration import TILE_TO_FILENAME

    TILES = (
        [f"{i}m" for i in range(1, 10)] + ["0m"]
        + [f"{i}p" for i in range(1, 10)] + ["0p"]
        + [f"{i}s" for i in range(1, 10)] + ["0s"]
        + ["1z", "2z", "3z", "4z", "5z", "6z", "7z"]
        + ["_", "?"]
    )

    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)

    print("使用 2D 绘制烘焙 3D 风格牌图（无需 OpenGL）...")
    ok = 0
    for tile in TILES:
        try:
            img = _render_tile_2d(tile, OUT_W, OUT_H)
            if tile == "_":
                name = "back"
            elif tile == "?":
                name = "unknown"
            else:
                name = TILE_TO_FILENAME.get(tile) or tile
            out_path = OUT_DIR / f"{name}.png"
            if img.save(str(out_path)):
                print(f"  OK: {tile} -> {out_path.name}")
                ok += 1
            else:
                print(f"  FAIL: {tile} (save)")
        except Exception as e:
            print(f"  FAIL: {tile} - {e}")

    print(f"\n完成: {ok}/{len(TILES)} 成功")
    return 0 if ok == len(TILES) else 1


if __name__ == "__main__":
    sys.exit(main())
