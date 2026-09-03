"""Integracja bez bake: OBJ -> LOD-y -> FBX (LZMESH2) + GLB + manifest."""
import json

from lodziarz.pipeline import ProcessOptions, process_asset

OBJ = """
mtllib glass.mtl
v 0 0 0
v 1 0 0
v 1 1 0
v 0 1 0
vt 0 0
vt 1 0
vt 1 1
vt 0 1
usemtl glass
f 1/1 2/2 3/3
f 1/1 3/3 4/4
"""

MTL = """
newmtl glass
Kd 0.6 0.8 0.9
d 0.3
"""

MANIFEST_KEYS = {"ok", "asset_name", "out_dir", "fbx", "fbx_per_lod", "obj",
                 "glb", "preview_glb", "textures", "lod_stats", "baked_mask",
                 "cache", "validation", "error"}


def test_process_no_bake_manifest_and_fbx(tmp_path, log):
    (tmp_path / "glass.obj").write_text(OBJ, encoding="utf-8")
    (tmp_path / "glass.mtl").write_text(MTL, encoding="utf-8")
    out = tmp_path / "out"
    opts = ProcessOptions(lod_count=2, bake=False, export_glb=True,
                          export_obj=False, lod_thresholds=[750.0])
    result = process_asset(tmp_path / "glass.obj", out, opts, log)
    assert result.ok, result.error
    assert result.baked_mask == [False, False]
    assert len(result.lod_stats) == 2
    # FBX przeszedl przez LZMESH2 (opacity_factor 0.3 z MTL) + progi z opcji
    assert (out / result.fbx).is_file()
    assert (out / result.glb).is_file()
    assert result.validation["materials"] == 1

    manifest = json.loads((out / "glass.lodziarz.json").read_text("utf-8"))
    assert MANIFEST_KEYS <= set(manifest.keys())
    assert manifest["ok"] is True
    assert manifest["validation"]["tris"] == 2
