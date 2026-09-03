"""Presety: klucze zgodne z ProcessOptions, flat FBX naprawde bez LODGroup."""
import dataclasses

from lodziarz.pipeline import ProcessOptions, process_asset
from lodziarz.presets import PRESETS

OBJ = """
v 0 0 0
v 1 0 0
v 1 1 0
v 0 1 0
vt 0 0
vt 1 0
vt 1 1
vt 0 1
f 1/1 2/2 3/3
f 1/1 3/3 4/4
"""

EXPECTED = {"unreal", "unity", "godot", "max", "loose"}


def test_preset_names_and_fields():
    assert set(PRESETS) == EXPECTED
    valid = {f.name for f in dataclasses.fields(ProcessOptions)}
    for name, preset in PRESETS.items():
        assert set(preset) <= valid, f"preset '{name}' ma obce pola"
        ProcessOptions(**preset)  # konstruowalne


def test_preset_semantics():
    assert PRESETS["unity"]["fbx_flat_lods"] is True
    assert PRESETS["unity"]["output_normal_directx"] is False
    assert PRESETS["godot"]["export_fbx"] is False
    assert PRESETS["godot"]["export_glb"] is True
    assert PRESETS["max"]["fbx_per_lod"] is True
    assert PRESETS["loose"]["orm_split"] is True
    # preset nie rusza niczego poza formatem/konwencjami
    off_limits = {"lod_count", "lod_ratio", "smooth_weld", "lod_thresholds",
                  "atlas_resolution", "ssaa", "dilation", "bake",
                  "baked_lods", "bake_backend", "input_normal_directx",
                  "texture_format", "up_axis"}
    for name, preset in PRESETS.items():
        assert not off_limits & set(preset), f"preset '{name}' rusza za duzo"


def _process(tmp_path, log, **kw):
    (tmp_path / "quad.obj").write_text(OBJ, encoding="utf-8")
    out = tmp_path / "out"
    opts = ProcessOptions(lod_count=2, bake=False, export_glb=False, **kw)
    result = process_asset(tmp_path / "quad.obj", out, opts, log)
    assert result.ok, result.error
    return out, result


def test_flat_fbx_has_no_lodgroup_node(tmp_path, log):
    out, result = _process(tmp_path, log, fbx_flat_lods=True)
    data = (out / result.fbx).read_bytes()
    assert b"LODGroup" not in data
    assert b"quad_LOD1" in data


def test_default_fbx_has_lodgroup_node(tmp_path, log):
    out, result = _process(tmp_path, log)
    data = (out / result.fbx).read_bytes()
    assert b"LODGroup" in data
    assert b"quad_LOD1" in data


def test_per_lod_without_main_fbx(tmp_path, log):
    # preset max/loose: tylko osobne pliki, bez zbiorczego LODGroup
    out, result = _process(tmp_path, log, export_fbx=False, fbx_per_lod=True)
    assert result.fbx == ""
    assert len(result.fbx_per_lod) == 2
    for name in result.fbx_per_lod:
        assert (out / name).is_file()
