"""Orkiestracja: import -> (bake) -> LOD-y -> export FBX/GLB/tekstury."""
from __future__ import annotations

import json
import traceback
from dataclasses import dataclass, field, asdict
from pathlib import Path

import numpy as np

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
    # ktore LOD-y dostaja atlas (indeksy); None = default: wszystkie OPROCZ LOD0
    # (LOD0 zwykle zostaje na oryginalnych materialach — bake dla niego to wybor)
    baked_lods: list | None = None
    bake_backend: str = "texel"
    up_axis: str = "auto"                # 'auto' | 'y' | 'z' — orientacja zrodla
    # reczne przypisania map z GUI: {material: {slot: sciezka}}
    material_textures: dict | None = None
    atlas_resolution: int = 1024
    dilation: int = 8
    ssaa: int = 2                        # antyaliasing bake: 1 (off) / 2 / 4
    cage_offset: float = 0.0             # raycast: inflacja cage w m (0 = auto)
    input_normal_directx: bool = True    # wejsciowe normalki DX (flip G przy bake)
    output_normal_directx: bool = True   # zapis normalki jako DX
    texture_format: str = "png"          # png | tga
    fbx_per_lod: bool = False            # dodatkowo osobne pliki SM_*_LODn.fbx
    fbx_embed_textures: bool = False     # tekstury wbudowane w FBX
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
    asset = load_asset(input_path, log, force_up=opts.up_axis)
    if opts.material_textures:
        from .matmap import apply_manual_assignments
        apply_manual_assignments(asset, opts.material_textures, log)
    result.asset_name = asset.name
    result.out_dir = str(out_dir)

    texture_files: dict = {}
    baked_images: dict = {}
    count = opts.lod_count

    # maska bake per LOD — pelna dowolnosc (np. [False, True, False, True])
    if not opts.bake:
        mask = [False] * count
    elif opts.baked_lods is None:
        mask = [i > 0 for i in range(count)]   # default: LOD0 oryginalny
    else:
        wanted = {int(i) for i in opts.baked_lods}
        mask = [i in wanted for i in range(count)]
    any_baked = any(mask)

    if opts.bake and not any_baked:
        log.info("zaden LOD nie zaznaczony do bake — pomijam bake")

    all_baked = all(mask)
    first_baked = mask.index(True) if any_baked else None

    # lancuch LOD z ORYGINALNEJ siatki — potrzebny dla LOD-ow bez bake
    # oraz jako geometria najgestszego bake'owanego LOD-a
    orig_chain = None
    if not all_baked or (first_baked or 0) > 0:
        log.progress(10, f"LOD-y ({count}, ratio {opts.lod_ratio})")
        orig_chain = build_lod_chain(asset.mesh, count=count,
                                     ratio=opts.lod_ratio, log=log)

    if any_baked:
        log.info("bake dla: " + ", ".join(f"LOD{i}" for i, b in enumerate(mask) if b)
                 + " | oryginalne materialy: "
                 + (", ".join(f"LOD{i}" for i, b in enumerate(mask) if not b) or "—"))
        # unwrap UV na najgestszym LOD-zie z bake; bardziej zdecymowane
        # dziedzicza ten atlas przez decymacje z zachowaniem UV
        base = asset.mesh if first_baked == 0 else orig_chain[first_baked]
        log.progress(15, f"unwrap UV LOD{first_baked} (xatlas)")
        baked_mesh, old_uvs = unwrap_atlas(base, opts.atlas_resolution,
                                           max(2, opts.dilation // 2), log)
        backend_name = opts.bake_backend
        if backend_name == "texel" and first_baked > 0:
            log.warn("texel-space wymaga topologii zrodla — LOD"
                     f"{first_baked} jest zdecymowany, przelaczam na raycast")
            backend_name = "raycast"
        log.progress(35, f"bake ({backend_name})")
        backend = get_backend(backend_name)
        # zrodlo projekcji: ORYGINALNA siatka (stare UV + materialy) —
        # material per texel wynika z trafienia na zrodle, per pixel
        bake_res = backend.bake(
            baked_mesh, old_uvs, asset.materials,
            BakeOptions(resolution=opts.atlas_resolution,
                        dilation=opts.dilation,
                        ssaa=opts.ssaa,
                        cage_offset=opts.cage_offset,
                        input_normal_flip_g=opts.input_normal_directx),
            log, source=asset.mesh)
        baked_images = bake_res.images
        log.progress(55, "zapis tekstur")
        texture_files = save_textures(
            baked_images, asset.name, out_dir, opts.texture_format,
            normal_directx=opts.output_normal_directx, log=log)
        save_debug(bake_res, asset.name, out_dir, log)

        log.progress(60, f"LOD-y z atlasem (od LOD{first_baked})")
        sub = build_lod_chain(baked_mesh, count=count - first_baked,
                              ratio=opts.lod_ratio, log=log,
                              label_start=first_baked)
        baked_chain = [None] * first_baked + sub

    if all_baked:
        materials = [MaterialData(name=f"M_{asset.name}")]
        baked_idx = 0
    elif any_baked:
        materials = list(asset.materials) + [MaterialData(name=f"M_{asset.name}")]
        baked_idx = len(asset.materials)
    else:
        materials = asset.materials
        baked_idx = None
    if any_baked:
        for m in baked_chain:
            if m is not None:
                m.tri_material = np.full(len(m.indices), baked_idx,
                                         dtype=np.int32)

    lods = [baked_chain[i] if mask[i] else orig_chain[i] for i in range(count)]
    chain = LodChain(asset_name=asset.name, lods=lods, materials=materials,
                     baked=mask, baked_material_index=baked_idx)
    result.lod_stats = [{"tris": int(m.triangle_count),
                         "verts": int(m.vertex_count)} for m in lods]

    log.progress(75, "export FBX")
    fbx_path = out_dir / f"{asset.name}.fbx"
    export_fbx_lodgroup(chain, fbx_path, texture_files, log,
                        embed=opts.fbx_embed_textures)
    result.fbx = fbx_path.name

    if opts.fbx_per_lod:
        log.progress(82, "export FBX per LOD")
        paths = export_fbx_per_lod(chain, out_dir, texture_files, log,
                                   embed=opts.fbx_embed_textures)
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
