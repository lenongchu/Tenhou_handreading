#!/usr/bin/env python3
"""
从 SVGtoasset 复制 3D 牌面素材到本项目的 assets/tile-assets

1. 若需重新生成：在 E:\\Cursor\\SVGtoasset 目录运行 python generate_mahjong.py
2. 复制 assets/*.png（3D 立牌）到 assets/tile-assets/Export/Regular/
   PNG 优先于 SVG，确保使用完整 3D 素材。
"""

from pathlib import Path

SOURCE = Path(r"E:\Cursor\SVGtoasset\assets")
FLAT_SOURCE = SOURCE / "flat"
DEST_PNG = Path(__file__).parent / "assets" / "tile-assets" / "Export" / "Regular"
DEST_FLAT = Path(__file__).parent / "assets" / "tile-assets" / "flat"
DEST_ASSETS = Path(__file__).parent / "assets" / "tile-assets"


def main():
    if not SOURCE.exists():
        print(f"错误：素材源目录不存在: {SOURCE}")
        return 1

    # 复制麻将桌背景
    table_src = SOURCE / "mahjong_table.png"
    if table_src.exists():
        (DEST_ASSETS / "mahjong_table.png").write_bytes(table_src.read_bytes())
        print("  OK: mahjong_table.png (背景)")

    # 复制 3D PNG（generate_mahjong 生成，含厚度、蓝底）
    DEST_PNG.mkdir(parents=True, exist_ok=True)
    count = 0
    for p in SOURCE.glob("*.png"):
        if "_flat" in p.name or p.name == "mahjong_table.png":
            continue
        t = DEST_PNG / p.name
        t.write_bytes(p.read_bytes())
        print(f"  OK: {p.name}")
        count += 1

    # 复制桌上视角（flat）素材
    if FLAT_SOURCE.exists():
        DEST_FLAT.mkdir(parents=True, exist_ok=True)
        for p in FLAT_SOURCE.glob("*.png"):
            t = DEST_FLAT / p.name
            t.write_bytes(p.read_bytes())
            print(f"  OK: flat/{p.name}")
            count += 1

    print(f"\n完成：已复制 {count} 个 PNG 到 assets/tile-assets")
    return 0


if __name__ == "__main__":
    exit(main())
