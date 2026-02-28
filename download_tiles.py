#!/usr/bin/env python3
"""
下载 riichi-mahjong-tiles 资源

优先下载 SVG 矢量图（立体效果最佳），其次 PNG。
用于麻将示意图生成。资源为公共领域 (CC0)。
"""

import urllib.request
from pathlib import Path

BASE_SVG = "https://raw.githubusercontent.com/FluffyStuff/riichi-mahjong-tiles/master/Regular"
BASE_PNG = "https://raw.githubusercontent.com/FluffyStuff/riichi-mahjong-tiles/master/Export/Regular"

TILE_NAMES = (
    [f"Man{i}" for i in range(1, 10)] + ["Man5-Dora"]
    + [f"Pin{i}" for i in range(1, 10)] + ["Pin5-Dora"]
    + [f"Sou{i}" for i in range(1, 10)] + ["Sou5-Dora"]
    + ["Ton", "Nan", "Shaa", "Pei", "Haku", "Hatsu", "Chun"]
)


def main():
    root = Path(__file__).parent
    svg_dir = root / "assets" / "riichi-mahjong-tiles" / "Regular"
    png_dir = root / "assets" / "riichi-mahjong-tiles" / "Export" / "Regular"
    svg_dir.mkdir(parents=True, exist_ok=True)
    png_dir.mkdir(parents=True, exist_ok=True)

    print("下载 SVG 矢量图（立体效果）...")
    for name in TILE_NAMES:
        path = svg_dir / f"{name}.svg"
        if path.exists():
            print(f"  跳过: {name}.svg")
            continue
        try:
            urllib.request.urlretrieve(f"{BASE_SVG}/{name}.svg", path)
            print(f"  OK: {name}.svg")
        except Exception as e:
            print(f"  FAIL: {name}.svg - {e}")

    print("\n下载 PNG（SVG 缺失时回退）...")
    for name in TILE_NAMES:
        path = png_dir / f"{name}.png"
        if path.exists():
            print(f"  跳过: {name}.png")
            continue
        try:
            urllib.request.urlretrieve(f"{BASE_PNG}/{name}.png", path)
            print(f"  OK: {name}.png")
        except Exception as e:
            print(f"  FAIL: {name}.png - {e}")

    print("\n完成。麻将示意图将使用 SVG 矢量牌面（立体效果）。")
    print("来源: FluffyStuff/riichi-mahjong-tiles (Public Domain)")


if __name__ == "__main__":
    main()
