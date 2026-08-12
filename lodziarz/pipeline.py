"""Orkiestracja: import -> (bake) -> LOD-y -> export FBX/GLB/tekstury."""
from __future__ import annotations

import json
import traceback
from dataclasses import dataclass, field, asdict
from pathlib import Path

from .atlas import unwrap_atlas
from .bake import BakeOptions, get_backend
from .core import LodChain, MaterialData
from .exporter.fbx import export_fbx_lodgroup, export_fbx_per_lod
from .exporter.glb import export_glb
from .exporter.textures import save_debug, save_textures
from .importer import load_asset
from .lod import build_lod_chain
from .logutil import PipelineLog


@dataclass
class ProcessOptions:
    lod_count: int = 4
    lod_ratio: float = 0.5
    bake: bool = True
    bake_backend: str = "texel"
    atlas_resolution: int = 2048
    dilation: int = 8
    ssaa: int = 2                        # antyaliasing bake: 1 (off) / 2 / 4
    input_normal_directx: bool = False   # wejsciowe normalki DX (flip G przy bake)
    output_normal_directx: bool = False  # zapis normalki jako DX
    texture_format: str = "png"          # png | tga
    fbx_per_lod: bool = False            # dodatkowo osobne pliki SM_*_LODn.fbx
    export_glb: bool = True


@dataclass
class ProcessResult:
    ok: bool = False
    asset_name: str = ""
    out_dir: str = ""
    fbx: str = ""
    fbx_per_lod: list = field(default_factory=list)
    glb: str = ""
    textures: dict = field(default_factory=dict)
    lod_stats: list = field(default_factory=list)  # [{tris, verts}, ...]
    error: str = ""


def process_asset(input_path: str | Path, out_dir: str | Path,
                  opts: ProcessOptions, log: PipelineLog) -> ProcessResult:
    result = ProcessResult()
    try:
        return _process(Path(input_path), Path(out_dir), opts, log, result)
    except Exception as e:
        log.error(f"{type(e).__name__}: {e}")
        for line in traceback.format_exc().splitlines()[-4:-1]:
            log.error(f"  {line.strip()}")
        result.error = str(e)
        return result


def _process(input_path: Path, out_dir: Path, opts: ProcessOptions,
             log: PipelineLog, result: ProcessResult) -> ProcessResult:
    out_dir.mkdir(parents=True, exist_ok=True)

    log.progress(2, "import")
    asset = load_asset(input_path, log)
    result.asset_name = asset.name
    result.out_dir = str(out_dir)

    texture_files: dict = {}
    baked_images: dict = {}
    mesh = asset.mesh
    materials = asset.materials

    if opts.bake:
        log.progress(15, "unwrap UV (xatlas)")
        mesh, old_uvs = unwrap_atlas(mesh, opts.atlas_resolution,
                                     max(2, opts.dilation // 2), log)
        log.progress(35, f"bake ({opts.bake_backend})")
        backend = get_backend(opts.bake_backend)
        bake_res = backend.bake(
            mesh, old_uvs, materials,
            BakeOptions(resolution=opts.atlas_resolution,
                        dilation=opts.dilation,
                        ssaa=opts.ssaa,
                        input_normal_flip_g=opts.input_normal_directx),
            log)
        baked_images = bake_res.images
        log.progress(55, "zapis tekstur")
        texture_files = save_textures(
            baked_images, asset.name, out_dir, opts.texture_format,
            normal_directx=opts.output_normal_directx, log=log)
        save_debug(bake_res, asset.name, out_dir, log)
        # po scaleniu: jeden material
        materials = [MaterialData(name=f"M_{asset.name}")]
        mesh.tri_material[:] = 0
    else:
        log.info("bake wylaczony — zachowuje oryginalne materialy i UV")

    log.progress(60, f"LOD-y ({opts.lod_count}, ratio {opts.lod_ratio})")
    lods = build_lod_chain(mesh, count=opts.lod_count,
                           ratio=opts.lod_ratio, log=log)
    chain = LodChain(asset_name=asset.name, lods=lods, materials=materials)
    result.lod_stats = [{"tris": int(m.triangle_count),
                         "verts": int(m.vertex_count)} for m in lods]

    log.progress(75, "export FBX")
    fbx_path = out_dir / f"{asset.name}.fbx"
    export_fbx_lodgroup(chain, fbx_path, texture_files, log)
    result.fbx = fbx_path.name

    if opts.fbx_per_lod:
        log.progress(82, "export FBX per LOD")
        paths = export_fbx_per_lod(chain, out_dir, texture_files, log)
        result.fbx_per_lod = [p.name for p in paths]

    if opts.export_glb:
        log.progress(90, "export GLB")
        glb_path = out_dir / f"{asset.name}.glb"
        export_glb(chain, glb_path, baked_images, log)
        result.glb = glb_path.name

    result.textures = texture_files
    result.ok = True

    manifest = out_dir / f"{asset.name}.lodziarz.json"
    manifest.write_text(json.dumps(asdict(result), indent=2), encoding="utf-8")
    log.progress(100, "gotowe")
    return result
