"""Import OBJ/MTL (d/Tr/map_d/map_Bump) i GLB (trimesh)."""
import numpy as np
from PIL import Image

from lodziarz.importer import load_asset

OBJ = """
mtllib test.mtl
v 0 0 0
v 1 0 0
v 1 1 0
v 0 1 0
v 0 0 1
v 1 0 1
vt 0 0
vt 1 0
vt 1 1
vt 0 1
usemtl matA
f 1/1 2/2 3/3
f 1/1 3/3 4/4
usemtl matB
f 5/1 6/2 3/3
"""

MTL = """
newmtl matA
Kd 0.8 0.2 0.2
map_Kd matA_bc.png
map_Bump matA_nm.png
map_d matA_op.png
d 0.5

newmtl matB
Kd 0.2 0.2 0.8
Tr 0.7
"""


def _png(path, color):
    Image.new("RGB", (4, 4), color).save(path)


def test_obj_mtl_opacity_and_bump(tmp_path, log):
    (tmp_path / "test.obj").write_text(OBJ, encoding="utf-8")
    (tmp_path / "test.mtl").write_text(MTL, encoding="utf-8")
    _png(tmp_path / "matA_bc.png", (200, 50, 50))
    _png(tmp_path / "matA_nm.png", (128, 128, 255))
    _png(tmp_path / "matA_op.png", (255, 255, 255))

    asset = load_asset(tmp_path / "test.obj", log)
    mats = {m.name: m for m in asset.materials}
    assert "matA" in mats and "matB" in mats
    a, b = mats["matA"], mats["matB"]
    # d 0.5 -> opacity_factor
    assert abs(a.opacity_factor - 0.5) < 1e-6
    assert a.opacity_tex is not None          # map_d
    assert a.normal_tex is not None           # map_Bump (wczesniej gubione)
    assert a.base_color_tex is not None       # map_Kd
    # Tr 0.7 -> opacity 0.3 (konwencja 3ds Max)
    assert abs(b.opacity_factor - 0.3) < 1e-6
    assert asset.mesh.triangle_count == 3


def test_glb_roundtrip(tmp_path, log):
    import trimesh
    box = trimesh.creation.box(extents=(1, 1, 1))
    box.visual = trimesh.visual.TextureVisuals(
        uv=np.zeros((len(box.vertices), 2)),
        material=trimesh.visual.material.PBRMaterial(
            name="M_Box", baseColorFactor=[255, 0, 0, 255]))
    glb = tmp_path / "box.glb"
    box.export(glb)

    asset = load_asset(glb, log)
    assert asset.mesh.triangle_count == 12
    assert len(asset.materials) == 1
    asset.mesh.validate()
