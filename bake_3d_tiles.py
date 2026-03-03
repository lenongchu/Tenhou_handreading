#!/usr/bin/env python3
"""
预烘焙 3D 风格麻将牌图

优先尝试 pyrender+3d_tile.glb（真 3D，需 OpenGL）；
失败则自动回退到 2D 绘制（立体感风格，无需 OpenGL）。
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

TILES = (
    [f"{i}m" for i in range(1, 10)] + ["0m"]
    + [f"{i}p" for i in range(1, 10)] + ["0p"]
    + [f"{i}s" for i in range(1, 10)] + ["0s"]
    + ["1z", "2z", "3z", "4z", "5z", "6z", "7z"]
    + ["_", "?"]
)
OUT_W, OUT_H = 168, 225


def _bake_with_pyrender() -> bool:
    """真 3D 渲染，需 OpenGL。成功返回 True。"""
    sys.path.insert(0, str(ROOT))
    from src.tile_illustration import TILE_TO_FILENAME, _tile_display_text, _load_character_image
    from src.tile_3d_renderer import render_tile_3d
    from PIL import Image

    # 先试一张，确认 pyrender 可用
    arr = render_tile_3d("7z", (OUT_W, OUT_H), char_img=None, text="中")
    if arr is None:
        return False
    ok = 0
    for tile in TILES:
        char_img = _load_character_image(tile) if tile not in ("_",) else None
        text = _tile_display_text(tile) if tile != "_" and not (char_img and not char_img.isNull()) else None
        arr = render_tile_3d(tile, (OUT_W, OUT_H), char_img=char_img, text=text)
        if arr is not None:
            name = "back" if tile == "_" else ("unknown" if tile == "?" else TILE_TO_FILENAME.get(tile) or tile)
            Image.fromarray(arr).save(OUT_DIR / f"{name}.png")
            print(f"  OK: {tile}")
            ok += 1
    print(f"\n完成（pyrender 3D）: {ok}/{len(TILES)}")
    return ok == len(TILES)


def _bake_with_2d() -> int:
    """2D 绘制 3D 风格，无需 OpenGL。"""
    import subprocess
    r = subprocess.run([sys.executable, str(ROOT / "bake_3d_tiles_2d.py")], cwd=str(ROOT))
    return r.returncode


def main():
    print("烘焙 3D 风格牌图...")
    print("尝试 pyrender（真 3D）...")
    if _bake_with_pyrender():
        return 0
    print("pyrender 不可用，回退到 2D 绘制...")
    return _bake_with_2d()


if __name__ == "__main__":
    sys.exit(main())
