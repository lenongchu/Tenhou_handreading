# Tile Glyph Assets

Put transparent glyph-only files in one of these folders:

- assets/tile-glyphs/
- assets/tile-glyphs/png/
- assets/tile-glyphs/svg/
- assets/mahjong-glyphs/
- assets/mahjong-glyphs/png/
- assets/mahjong-glyphs/svg/

Use these filenames (example): `Man1.png`, `Pin5-Dora.png`, `Ton.png`.

The renderer now works as:

1. Use glyph-only assets first.
2. If missing, fallback to legacy full-tile art and auto-extract symbols.


Default source priority now includes FluffyStuff Regular SVG:

1. `assets/tile-glyphs*` / `assets/mahjong-glyphs*` (if you provide glyph-only files)
2. `assets/riichi-mahjong-tiles/Regular/*.svg` (your requested source)
3. legacy full-tile export assets with auto symbol extraction
