"""Bake przez raycast z cage'a.

Per texel nowego atlasu: pozycja/normala z G-buffera (rasteryzacja nowych UV
celu), inflacja wzdluz normali o cage_offset, raycast w strone powierzchni
ZRODLA (BVH trimesha; source = oryginalna siatka, np. LOD0), sampling
materialow zrodlowych w punkcie trafienia (barycentryczne UV zrodla).
Material per texel = tri_material trafionego trojkata zrodla — granice
materialow sa per pixel, niezalezne od topologii celu. Normalki: mapa
zrodlowa -> world (TBN trojkata trafionego) -> tangent space celu.

Raycast po CPU — wolniejszy od texel-space, ale jedyny poprawny gdy cel
ma inna topologie niz zrodlo (bake na zdecymowanym LOD-zie).
"""
from __future__ import annotations

import numpy as np
from PIL import Image

from . import BakeBackend, BakeOptions, BakeResult
from .common import (compose_material_arrays, decode_normal_map, dilate,
                     draw_uv_layout, encode_to_tangent_space, render_gbuffer,
                     sample_nearest, tbn_to_world)
from ..core import MaterialData, MeshData, compute_tangents
from ..logutil import PipelineLog


class RaycastCageBackend(BakeBackend):
    name = "raycast"

    def bake(self, mesh: MeshData, old_uvs: np.ndarray,
             materials: list[MaterialData], opts: BakeOptions,
             log: PipelineLog, source: MeshData | None = None) -> BakeResult:
        import moderngl
        import trimesh

        src = source if source is not None else mesh
        src_uvs = src.uvs if source is not None else old_uvs

        res = int(np.clip(opts.resolution, 64, 4096))
        ext = src.positions.max(axis=0) - src.positions.min(axis=0)
        diag = float(np.linalg.norm(ext)) or 1.0
        cage = opts.cage_offset if opts.cage_offset > 0 else diag * 0.01
        log.info(f"bake raycast-cage: {res}px, cage offset {cage:.4f} m, "
                 f"zrodlo {len(src.indices)} tri -> cel {len(mesh.indices)} tri")

        ctx = moderngl.create_context(standalone=True)
        try:
            gbuf = render_gbuffer(ctx, mesh, res)
        finally:
            ctx.release()

        cov = gbuf["coverage"]
        idx = np.argwhere(cov)                    # (N,2) wiersz,kolumna
        pos = gbuf["pos"][cov]
        nrm = gbuf["normal"][cov]
        tan = gbuf["tangent"][cov]
        n_tex = len(idx)
        log.info(f"pokrycie atlasu: {cov.mean() * 100:.1f}% "
                 f"({n_tex} texeli do raycastu)")

        src_tm = trimesh.Trimesh(vertices=src.positions.astype(np.float64),
                                 faces=src.indices.astype(np.int64),
                                 process=False)
        caster = _make_caster(src_tm, log)

        origins = pos + nrm * cage
        dirs = -nrm.astype(np.float64)

        hit_tri = np.full(n_tex, -1, dtype=np.int64)
        hit_pt = np.zeros((n_tex, 3), dtype=np.float64)
        chunk = 65536
        for start in range(0, n_tex, chunk):
            end = min(start + chunk, n_tex)
            loc, ray_i, tri_i = caster.intersects_location(
                origins[start:end].astype(np.float64), dirs[start:end],
                multiple_hits=False)
            if len(ray_i):
                hit_tri[start + ray_i] = tri_i
                hit_pt[start + ray_i] = loc
            log.progress(35 + 15 * end / n_tex, f"raycast {end}/{n_tex}")

        hit = hit_tri >= 0
        miss = int((~hit).sum())
        if miss:
            log.warn(f"raycast: {miss} texeli bez trafienia "
                     f"({miss / n_tex * 100:.1f}%) — wypelni je dilation")

        # barycentryczne UV zrodla + material w punkcie trafienia (per pixel)
        src_tan = compute_tangents(src, src_uvs)
        tris = src.indices[hit_tri[hit]]                        # (H,3)
        v0 = src.positions[tris[:, 0]].astype(np.float64)
        v1 = src.positions[tris[:, 1]].astype(np.float64)
        v2 = src.positions[tris[:, 2]].astype(np.float64)
        bary = _barycentric(hit_pt[hit], v0, v1, v2)            # (H,3)

        def interp(attr: np.ndarray) -> np.ndarray:
            return (attr[tris[:, 0]] * bary[:, 0:1]
                    + attr[tris[:, 1]] * bary[:, 1:2]
                    + attr[tris[:, 2]] * bary[:, 2:3])

        uv_hit = interp(src_uvs.astype(np.float64))
        nrm_hit = interp(src.normals.astype(np.float64))
        tan_hit = interp(src_tan.astype(np.float64))
        mat_hit = src.tri_material[hit_tri[hit]]

        # sampling materialow per texel
        mat_arrays = [compose_material_arrays(m) for m in materials]
        any_emissive = any(m.has_emissive() for m in materials)
        any_opacity = any(m.has_opacity() for m in materials)

        out = {k: np.zeros((res, res, 3), dtype=np.uint8)
               for k in ("basecolor", "normal", "orm", "emissive", "opacity")}
        out["normal"][:, :] = (128, 128, 255)
        hit_rows = idx[hit]

        for mid in np.unique(mat_hit):
            sel = mat_hit == mid
            arrays = mat_arrays[int(mid)] if 0 <= mid < len(mat_arrays) \
                else mat_arrays[0]
            uvs_sel = uv_hit[sel]
            rows = hit_rows[sel]
            for key in ("basecolor", "orm", "emissive", "opacity"):
                smp = sample_nearest(arrays[key], uvs_sel)[:, :3]
                out[key][rows[:, 0], rows[:, 1]] = smp
            # normalki: stary TS -> world -> nowy TS
            n_ts = decode_normal_map(sample_nearest(arrays["normal"], uvs_sel),
                                     opts.input_normal_flip_g)
            n_world = tbn_to_world(n_ts, nrm_hit[sel], tan_hit[sel])
            enc = encode_to_tangent_space(n_world, nrm[hit][sel], tan[hit][sel])
            out["normal"][rows[:, 0], rows[:, 1]] = enc

        images = {}
        eff_cov = np.zeros((res, res), dtype=bool)
        eff_cov[hit_rows[:, 0], hit_rows[:, 1]] = True
        for key in ("basecolor", "normal", "orm", "emissive", "opacity"):
            if key == "emissive" and not any_emissive:
                continue
            if key == "opacity" and not any_opacity:
                continue
            img = dilate(out[key], eff_cov, max(opts.dilation, 2))
            if key == "opacity":
                images[key] = Image.fromarray(img[:, :, 0], "L")
            else:
                images[key] = Image.fromarray(img, "RGB")

        cov_img = Image.fromarray((eff_cov * 255).astype(np.uint8), "L")
        return BakeResult(images=images, coverage=cov_img,
                          uv_layout=draw_uv_layout(mesh, res))


def _make_caster(source, log: PipelineLog):
    try:
        from trimesh.ray.ray_pyembree import RayMeshIntersector
        log.info("raycast: embree")
        return RayMeshIntersector(source)
    except BaseException:
        log.info("raycast: trimesh (python) — dla duzych atlasow bedzie wolno")
        from trimesh.ray.ray_triangle import RayMeshIntersector
        return RayMeshIntersector(source)


def _barycentric(p: np.ndarray, a: np.ndarray, b: np.ndarray,
                 c: np.ndarray) -> np.ndarray:
    v0 = b - a
    v1 = c - a
    v2 = p - a
    d00 = np.sum(v0 * v0, axis=1)
    d01 = np.sum(v0 * v1, axis=1)
    d11 = np.sum(v1 * v1, axis=1)
    d20 = np.sum(v2 * v0, axis=1)
    d21 = np.sum(v2 * v1, axis=1)
    denom = d00 * d11 - d01 * d01
    denom[np.abs(denom) < 1e-20] = 1e-20
    v = (d11 * d20 - d01 * d21) / denom
    w = (d00 * d21 - d01 * d20) / denom
    u = 1.0 - v - w
    return np.clip(np.stack([u, v, w], axis=1), 0.0, 1.0)
