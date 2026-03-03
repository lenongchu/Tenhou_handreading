"""
Bake 3D tile GLB into a 2D transparent base sprite for the illustration renderer.

Usage:
    py -3.12 scripts/bake_model_tile.py
"""

from pathlib import Path
import argparse


def bake(
    glb_path: Path,
    out_path: Path,
    resolution: int = 900,
    angle_x: float = 1.38,
    angle_y: float = 0.0,
    angle_z: float = 0.16,
    distance: float = 2.8,
) -> None:
    import trimesh
    from PIL import Image
    import numpy as np

    scene = trimesh.load(glb_path, force="scene")
    if not isinstance(scene, trimesh.Scene):
        scene = trimesh.Scene(scene)

    scene.rezero()
    scene.set_camera(angles=(angle_x, angle_y, angle_z), distance=distance, center=scene.centroid)
    png = scene.save_image(resolution=(resolution, resolution), visible=True)

    tmp = out_path.with_suffix(".tmp.png")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_bytes(png)

    img = Image.open(tmp).convert("RGBA")
    arr = np.array(img)
    bg = arr[0, 0, :3].astype(int)
    diff = np.abs(arr[:, :, :3].astype(int) - bg).max(axis=2)
    mask = diff > 8
    ys, xs = np.where(mask)
    if len(xs) == 0:
        raise RuntimeError("Could not find model pixels in rendered image")

    minx, maxx = xs.min(), xs.max()
    miny, maxy = ys.min(), ys.max()
    pad = 8
    minx = max(0, minx - pad)
    miny = max(0, miny - pad)
    maxx = min(arr.shape[1] - 1, maxx + pad)
    maxy = min(arr.shape[0] - 1, maxy + pad)

    crop = arr[miny:maxy + 1, minx:maxx + 1].copy()
    diff2 = np.abs(crop[:, :, :3].astype(int) - bg).max(axis=2)
    alpha = np.where(diff2 <= 8, 0, 255).astype(np.uint8)
    crop[:, :, 3] = alpha

    Image.fromarray(crop, "RGBA").save(out_path)
    tmp.unlink(missing_ok=True)


def main() -> None:
    repo = Path(__file__).resolve().parents[1]
    ap = argparse.ArgumentParser(description="Bake 3D tile GLB to 2D sprite")
    ap.add_argument("--input", default=str(repo / "3d_tile.glb"), help="Input GLB path")
    ap.add_argument(
        "--output",
        default=str(repo / "assets" / "3d-tile" / "base_tile_glb.png"),
        help="Output PNG path",
    )
    ap.add_argument("--resolution", type=int, default=900, help="Render resolution")
    ap.add_argument("--angle-x", type=float, default=1.38, help="Camera angle X (radians)")
    ap.add_argument("--angle-y", type=float, default=0.0, help="Camera angle Y (radians)")
    ap.add_argument("--angle-z", type=float, default=0.16, help="Camera angle Z (radians)")
    ap.add_argument("--distance", type=float, default=2.8, help="Camera distance")
    args = ap.parse_args()

    bake(
        Path(args.input),
        Path(args.output),
        resolution=args.resolution,
        angle_x=args.angle_x,
        angle_y=args.angle_y,
        angle_z=args.angle_z,
        distance=args.distance,
    )
    print(f"Baked: {args.output}")


if __name__ == "__main__":
    main()
