#!/usr/bin/env python3
"""Inspect 3d_tile.glb: mesh info, materials, textures, UV."""

import os

try:
    import trimesh
except ImportError:
    print("ERROR: trimesh not installed. Run: pip install trimesh")
    exit(1)

# Resolve glb path (workspace root or assets)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PATHS = [
    os.path.join(SCRIPT_DIR, "3d_tile.glb"),
    os.path.join(SCRIPT_DIR, "assets", "3d_tile.glb"),
]
glb_path = None
for p in PATHS:
    if os.path.isfile(p):
        glb_path = p
        break

if not glb_path:
    print("ERROR: 3d_tile.glb not found in workspace root or assets/")
    exit(1)

print(f"Loading: {glb_path}\n")

scene = trimesh.load(glb_path, force="scene")

# --- Meshes ---
print("=" * 60)
print("MESH INFO")
print("=" * 60)

def dump_mesh(mesh, name="mesh", indent=0):
    prefix = "  " * indent
    if isinstance(mesh, trimesh.Scene):
        for n, g in mesh.geometry.items():
            dump_mesh(g, name=n, indent=indent)
        return
    if not isinstance(mesh, trimesh.Trimesh):
        print(f"{prefix}{name}: (not a Trimesh, type={type(mesh).__name__})")
        return
    print(f"{prefix}{name}:")
    print(f"{prefix}  vertices: {len(mesh.vertices)}")
    print(f"{prefix}  faces: {len(mesh.faces)}")
    print(f"{prefix}  bounds: {mesh.bounds}")
    if hasattr(mesh, "visual") and mesh.visual is not None:
        v = mesh.visual
        print(f"{prefix}  visual type: {type(v).__name__}")
        if hasattr(v, "material"):
            print(f"{prefix}  material: {v.material}")
        if hasattr(v, "uv"):
            uv = getattr(v, "uv", None)
            if uv is not None:
                print(f"{prefix}  UV shape: {v.uv.shape}")
                print(f"{prefix}  UV sample: {v.uv[:3]}")
            else:
                print(f"{prefix}  UV: None")

dump_mesh(scene)

# --- Materials ---
print("\n" + "=" * 60)
print("MATERIALS")
print("=" * 60)

if hasattr(scene, "graph") and scene.graph is not None:
    for name, geom in scene.geometry.items():
        if hasattr(geom, "visual") and geom.visual is not None:
            v = geom.visual
            if hasattr(v, "material") and v.material is not None:
                m = v.material
                print(f"\n[{name}]")
                if hasattr(m, "__dict__"):
                    for k, val in m.__dict__.items():
                        if not k.startswith("_"):
                            print(f"  {k}: {val}")
                else:
                    print(f"  {m}")

# Try trimesh.exchange.gltf for richer material/data
try:
    from trimesh.exchange import gltf
    data = gltf.load_gltf(glb_path)
    if "materials" in data:
        print("\n[GLTF materials]")
        for i, m in enumerate(data.get("materials", [])):
            print(f"  material[{i}]: {m}")
    if "images" in data or "textures" in data:
        print("\n[GLTF textures/images]")
        for k in ("textures", "images"):
            if k in data:
                for i, t in enumerate(data[k]):
                    print(f"  {k}[{i}]: {t}")
except Exception as e:
    print(f"(gltf loader detail: {e})")

# --- UV ---
print("\n" + "=" * 60)
print("UV COORDINATES")
print("=" * 60)

for name, geom in scene.geometry.items():
    if isinstance(geom, trimesh.Trimesh) and hasattr(geom, "visual") and geom.visual is not None:
        uv = getattr(geom.visual, "uv", None)
        if uv is not None:
            print(f"\n[{name}] UV shape={uv.shape}")
            print(f"  range: [{uv.min(axis=0)}] to [{uv.max(axis=0)}]")
            print(f"  sample: {uv[:5]}")
        else:
            print(f"\n[{name}]: no UV")

# Summary
print("\n" + "=" * 60)
print("SUMMARY")
print("=" * 60)
print(f"Geometry count: {len(scene.geometry)}")
for n, g in scene.geometry.items():
    if isinstance(g, trimesh.Trimesh):
        has_uv = hasattr(g, "visual") and g.visual is not None and getattr(g.visual, "uv", None) is not None
        print(f"  - {n}: {len(g.vertices)} verts, {len(g.faces)} faces, UV={has_uv}")

