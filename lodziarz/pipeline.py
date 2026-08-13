"""Orkiestracja: import -> (bake) -> LOD-y -> export FBX/GLB/tekstury."""
from __future__ import annotations

import json
import traceback
from dataclasses import dataclass, field, asdict
from pathlib import Path

import numpy as np

from .atlas import texel_density_hint, unwrap_atlas
from .bake import BakeOptions, get_backend
from .core import LodChain, MaterialData
from .exporter.fbx import export_fbx_lodgroup, export_fbx_per_lod
from .exporter.glb import export_glb
from .exporter.textures import flip_normal_g, save_debug, save_textures
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
    orm_split: bool = False              # AO/Roughness/Metallic osobno zamiast ORM
    output_gloss: bool = False           # przy orm_split: glossiness zamiast roughness
    fbx_per_lod: bool = False            # dodatkowo osobne pliki SM_*_LODn.fbx
    fbx_embed_textures: bool = False     # tekstury wbudowane w FBX
    # formaty wyjscia wg wyboru uzytkownika (viewer i tak dostaje swoj
    # podglad GLB w debug/, niezaleznie od tych flag)
    export_fbx: bool = True
    export_glb: bool = True
    export_obj: bool = False


@dataclass
class ProcessResult:
    ok: bool = False
    asset_name: str = ""
    out_dir: str = ""
    fbx: str = ""
    fbx_per_lod: list = field(default_factory=list)
    obj: list = field(default_factory=list)
    glb: str = ""
    preview_glb: str = ""   # GLB z oboma wariantami per LOD (viewer)
    textures: dict = field(default_factory=dict)
    lod_stats: list = field(default_factory=list)  # [{tris, verts}, ...]
    baked_mask: list = field(default_factory=list)  # bake per LOD (bool)
    cache: str = ""     # plik cache do re-exportu FBX z inna maska
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

    if any_baked:
        log.info("bake dla: " + ", ".join(f"LOD{i}" for i, b in enumerate(mask) if b)
                 + " | oryginalne materialy: "
                 + (", ".join(f"LOD{i}" for i, b in enumerate(mask) if not b) or "—"))
        # unwrap + bake ZAWSZE na oryginalnej siatce (pelna jakosc, texel
        # dziala 1:1); zdecymowane LOD-y dziedzicza atlas przez decymacje
        # z zachowaniem UV. Maska tylko wybiera, ktore LOD-y go uzywaja.
        log.progress(15, "unwrap UV (xatlas)")
        baked_mesh, old_uvs = unwrap_atlas(asset.mesh, opts.atlas_resolution,
                                           max(2, opts.dilation // 2), log)
        texel_density_hint(baked_mesh, old_uvs, asset.materials,
                           opts.atlas_resolution, log)
        log.progress(35, f"bake ({opts.bake_backend})")
        backend = get_backend(opts.bake_backend)
        # source: oryginalna siatka — material per texel per pixel
        # (dla texel zrodlem sa stare UV celu, topologia identyczna)
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
            normal_directx=opts.output_normal_directx,
            orm_split=opts.orm_split, gloss=opts.output_gloss, log=log)
        save_debug(bake_res, asset.name, out_dir, log)

    # 2 sety LOD-ow: z atlasem (z unwrapped LOD0) i oryginalny — maska
    # per LOD wybiera, ktory set trafia do wyjscia; oba sety ida do GLB,
    # wiec w viewerze mozna przelaczac bake per LOD juz po operacji
    log.progress(60, f"LOD-y ({count}, ratio {opts.lod_ratio})")
    baked_chain = None
    if any_baked:
        baked_chain = build_lod_chain(baked_mesh, count=count,
                                      ratio=opts.lod_ratio, log=log)
    orig_chain = build_lod_chain(asset.mesh, count=count,
                                 ratio=opts.lod_ratio, log=log)

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
            m.tri_material = np.full(len(m.indices), baked_idx, dtype=np.int32)

    lods = [baked_chain[i] if mask[i] else orig_chain[i] for i in range(count)]
    chain = LodChain(asset_name=asset.name, lods=lods, materials=materials,
                     baked=mask, baked_material_index=baked_idx)
    result.lod_stats = [{"tris": int(m.triangle_count),
                         "verts": int(m.vertex_count)} for m in lods]

    if opts.export_fbx:
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

    if opts.export_obj:
        log.progress(86, "export OBJ")
        from .exporter.obj import export_obj
        paths = export_obj(chain, out_dir, texture_files, log)
        result.obj = [p.name for p in paths]

    log.progress(90, "export GLB")
    # normalka w GLB w konwencji wyjsciowej (spojnie z plikami tekstur
    # i defaultem wyswietlania w viewerze)
    glb_images = dict(baked_images)
    if opts.output_normal_directx and glb_images.get("normal") is not None:
        glb_images["normal"] = flip_normal_g(glb_images["normal"])
    if opts.export_glb:
        glb_path = out_dir / f"{asset.name}.glb"
        export_glb(chain, glb_path, glb_images, log)
        result.glb = glb_path.name
    # podglad dla viewera — zawsze, niezaleznie od formatow wyjscia;
    # przy bake oba warianty per LOD (przelacznik w viewerze)
    dbg = out_dir / "debug"
    dbg.mkdir(parents=True, exist_ok=True)
    if any_baked:
        dual = {"baked": baked_chain, "orig": orig_chain,
                "orig_materials": asset.materials}
        export_glb(chain, dbg / f"{asset.name}_variants.glb", glb_images,
                   log, dual=dual)
        result.preview_glb = f"debug/{asset.name}_variants.glb"
    else:
        export_glb(chain, dbg / f"{asset.name}_preview.glb", glb_images, log)
        result.preview_glb = f"debug/{asset.name}_preview.glb"

    if any_baked:
        # cache do re-exportu FBX/GLB z inna maska bez ponownego bake
        from .cache import save_cache
        cache_name = f"{asset.name}.lodziarz_cache.pkl"
        save_cache(out_dir / cache_name, asset_name=asset.name,
                   baked_lods=baked_chain, orig_lods=orig_chain,
                   materials=asset.materials, texture_files=texture_files,
                   embed=opts.fbx_embed_textures, per_lod=opts.fbx_per_lod,
                   formats={"fbx": opts.export_fbx, "glb": opts.export_glb,
                            "obj": opts.export_obj})
        result.cache = cache_name

    result.baked_mask = list(mask)
    result.textures = texture_files
    result.ok = True

    manifest = out_dir / f"{asset.name}.lodziarz.json"
    manifest.write_text(json.dumps(asdict(result), indent=2), encoding="utf-8")
    log.progress(100, "gotowe")
    return result
