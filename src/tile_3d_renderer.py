"""
3D 麻将牌渲染器

使用 3d_tile.glb 模型，将字符贴到牌面后渲染为 PNG。
参照参考图：牌体略倾角、可见右侧边和顶边，字符居中、大小适中。
"""

import os
from pathlib import Path
from typing import Optional, Tuple, Union

import numpy as np


def _qimage_to_rgba(qimg: "object") -> Optional["np.ndarray"]:
    """QImage -> numpy RGBA (H,W,4) uint8"""
    try:
        from PyQt5.QtGui import QImage
        from PyQt5.QtCore import QBuffer, QIODevice
        if not isinstance(qimg, QImage) or qimg.isNull():
            return None
        if qimg.format() != QImage.Format_ARGB32:
            qimg = qimg.convertToFormat(QImage.Format_ARGB32)
        buf = QBuffer()
        buf.open(QBuffer.WriteOnly)
        qimg.save(buf, "PNG")
        buf.close()
        from PIL import Image
        import io
        pil = Image.open(io.BytesIO(buf.data().data()))
        return np.array(pil.convert("RGBA"))
    except Exception:
        return None


def _find_glb_path() -> Optional[Path]:
    root = Path(__file__).parent.parent
    for p in [root / "3d_tile.glb", root / "assets" / "3d_tile.glb", root / "assets" / "3d-tile" / "3d_tile.glb"]:
        if p.exists():
            return p
    return None


def _create_face_texture_pil(
    char_img_rgba: Optional["np.ndarray"],
    text: Optional[str],
    tex_size: int = 512,
    char_scale: float = 0.58,
) -> "object":
    """
    创建牌面纹理：象牙白底 + 居中字符（参照图：字符适中、留边）。
    """
    try:
        from PIL import Image
        from PIL import ImageDraw
        from PIL import ImageFont
    except ImportError:
        return None
    img = Image.new("RGBA", (tex_size, tex_size), (253, 250, 242, 255))
    draw = ImageDraw.Draw(img)
    cx, cy = tex_size // 2, tex_size // 2
    margin = int(tex_size * (1 - char_scale) / 2)
    dst = (margin, margin, tex_size - margin, tex_size - margin)
    dst_w, dst_h = dst[2] - dst[0], dst[3] - dst[1]
    if char_img_rgba is not None and char_img_rgba.size > 0:
        try:
            char_pil = Image.fromarray(char_img_rgba, "RGBA")
            char_pil = char_pil.resize((dst_w, dst_h), Image.Resampling.LANCZOS)
            img.paste(char_pil, dst[:2], char_pil)
        except Exception:
            pass
    elif text:
        font = ImageFont.load_default()
        for fn in ("msyh.ttc", "msyhbd.ttc", "simhei.ttf", "arial.ttf"):
            try:
                font = ImageFont.truetype(fn, int(tex_size * 0.38))
                break
            except Exception:
                pass
        bbox = draw.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text((cx - tw // 2, cy - th // 2), text, fill=(40, 35, 30, 255), font=font)
    return img


def render_tile_3d(
    tile_str: str,
    output_size: Tuple[int, int],
    char_img: Optional["object"] = None,
    text: Optional[str] = None,
) -> Optional["np.ndarray"]:
    """
    渲染单张 3D 牌。
    tile_str: 牌符号 (1m, 5z 等)，_ 为牌背
    output_size: (width, height)
    char_img: QImage 或 numpy RGBA，字符图（可为 None，则用 text）
    text: 无 char_img 时的手绘文字

    Returns: numpy RGB (H,W,3) uint8，或 None
    """
    try:
        import trimesh
        import pyrender
        from PIL import Image
    except ImportError:
        return None

    glb_path = _find_glb_path()
    if not glb_path:
        return None

    scene_trimesh = trimesh.load(str(glb_path), force="scene")
    if not scene_trimesh.geometry:
        return None

    geom_name = list(scene_trimesh.geometry.keys())[0]
    mesh = scene_trimesh.geometry[geom_name].copy()
    if not isinstance(mesh, trimesh.Trimesh):
        return None
    if getattr(mesh.visual, "uv", None) is None:
        return None

    # 牌背：绿色渐变纹理
    if tile_str == "_":
        arr = np.zeros((512, 512, 4), dtype=np.uint8)
        g = np.clip(55 + (np.arange(512)[:, None] + np.arange(512)) / 4, 0, 255).astype(np.uint8) % 80
        arr[:, :, 0] = 35
        arr[:, :, 1] = 70 + g
        arr[:, :, 2] = 50
        arr[:, :, 3] = 255
        tex = Image.fromarray(arr, "RGBA")
    else:
        char_arr = None
        if char_img is not None:
            char_arr = _qimage_to_rgba(char_img)
            if char_arr is None and hasattr(char_img, "__array__"):
                char_arr = np.asarray(char_img)
        tex = _create_face_texture_pil(char_arr, text or "?")
        if tex is None:
            return None

    mat = trimesh.visual.material.PBRMaterial(baseColorTexture=tex)
    mesh.visual = trimesh.visual.TextureVisuals(uv=mesh.visual.uv, material=mat)

    scene = pyrender.Scene(ambient_light=[0.6, 0.6, 0.6], bg_color=[255, 255, 255])
    mesh_py = pyrender.Mesh.from_trimesh(mesh, smooth=False)
    scene.add(mesh_py)

    # 相机：参照图，正面略倾，可见右侧边和顶边。牌 xz 为主面，y 为厚度
    cam = pyrender.PerspectiveCamera(yfov=np.pi / 4.5, aspectRatio=float(output_size[0]) / max(1, output_size[1]))
    # 相机在 y+ 侧略偏右偏上，看向牌心
    eye = np.array([0.12, 0.5, 0.02])
    target = np.array([0.0, 0.0, 0.0])
    up = np.array([0, 0, 1])
    fwd = target - eye
    fwd = fwd / (np.linalg.norm(fwd) + 1e-8)
    right = np.cross(fwd, up)
    right = right / (np.linalg.norm(right) + 1e-8)
    up = np.cross(right, fwd)
    cam_pose = np.eye(4)
    cam_pose[0:3, 0] = right
    cam_pose[0:3, 1] = -up
    cam_pose[0:3, 2] = -fwd
    cam_pose[0:3, 3] = eye
    scene.add(cam, pose=cam_pose)

    light = pyrender.DirectionalLight(color=[1, 1, 1], intensity=2.0)
    light_pose = np.eye(4)
    light_pose[0:3, 3] = [0.2, 0.5, 0.5]
    scene.add(light, pose=light_pose)

    try:
        r = pyrender.OffscreenRenderer(output_size[0], output_size[1])
        color, _ = r.render(scene)
        r.delete()
    except Exception as e:
        import logging
        logging.getLogger(__name__).debug("pyrender OffscreenRenderer failed: %s", e)
        return None

    return color
