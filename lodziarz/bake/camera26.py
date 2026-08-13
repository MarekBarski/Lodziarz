"""Bake przez projekcje z 26 kamer ortho wokol obiektu.

26 kierunkow = srodki scian/krawedzi/rogow szescianu wokol AABB. Kazda kamera
renderuje ZRODLO (source = oryginalna siatka: jej UV + materialy) do 5 map
(basecolor, normal world, orm, emissive, opacity) + depth. Per texel nowego
atlasu CELU: pozycja/normala z G-buffera -> wybor najlepszej widocznej kamery
(dot(n, -dir), test depth), reprojekcja i sampling renderu. Granice materialow
sa per pixel (ze screen-space renderu zrodla), niezalezne od topologii celu.
"""
from __future__ import annotations

import numpy as np
from PIL import Image

from . import BakeBackend, BakeOptions, BakeResult
from .common import (dilate, draw_uv_layout, encode_to_tangent_space,
                     material_gl_textures, render_gbuffer)
from ..core import MaterialData, MeshData, compute_tangents
from ..logutil import PipelineLog

CAM_VS = """
#version 330
uniform mat4 uViewProj;
in vec3 in_pos;
in vec2 in_old_uv;
in vec3 in_normal;
in vec4 in_old_tan;
out vec2 vUv;
out vec3 vN;
out vec4 vTan;
void main() {
    vUv = in_old_uv;
    vN = in_normal;
    vTan = in_old_tan;
    gl_Position = uViewProj * vec4(in_pos, 1.0);
}
"""

CAM_FS = """
#version 330
uniform sampler2D uBaseColor;
uniform sampler2D uNormal;
uniform sampler2D uOrm;
uniform sampler2D uEmissive;
uniform sampler2D uOpacity;
uniform bool uFlipInputG;
in vec2 vUv;
in vec3 vN;
in vec4 vTan;
layout(location = 0) out vec4 outBaseColor;
layout(location = 1) out vec4 outNormal;   // world-space, 0..1
layout(location = 2) out vec4 outOrm;
layout(location = 3) out vec4 outEmissive;
layout(location = 4) out vec4 outOpacity;
void main() {
    outBaseColor = vec4(texture(uBaseColor, vUv).rgb, 1.0);
    outOrm = vec4(texture(uOrm, vUv).rgb, 1.0);
    outEmissive = vec4(texture(uEmissive, vUv).rgb, 1.0);
    outOpacity = vec4(texture(uOpacity, vUv).rgb, 1.0);
    vec3 nts = texture(uNormal, vUv).xyz * 2.0 - 1.0;
    if (uFlipInputG) nts.y = -nts.y;
    vec3 N = normalize(vN);
    vec3 T = vTan.xyz - N * dot(N, vTan.xyz);
    T = length(T) > 1e-6 ? normalize(T) : vec3(1.0, 0.0, 0.0);
    vec3 B = cross(N, T) * vTan.w;
    vec3 nws = normalize(T * nts.x + B * nts.y + N * nts.z);
    outNormal = vec4(nws * 0.5 + 0.5, 1.0);
}
"""


def _camera_dirs() -> np.ndarray:
    dirs = []
    for x in (-1, 0, 1):
        for y in (-1, 0, 1):
            for z in (-1, 0, 1):
                if x == y == z == 0:
                    continue
                v = np.array([x, y, z], dtype=np.float64)
                dirs.append(v / np.linalg.norm(v))
    return np.array(dirs)   # (26,3) — kierunek PATRZENIA kamery = -dir


def _ortho_viewproj(direction: np.ndarray, center: np.ndarray,
                    radius: float) -> np.ndarray:
    """Kamera w center + direction*2r patrzaca na center, ortho +-r."""
    eye = center + direction * radius * 2.0
    fwd = -direction
    up = np.array([0.0, 1.0, 0.0])
    if abs(np.dot(fwd, up)) > 0.99:
        up = np.array([1.0, 0.0, 0.0])
    right = np.cross(fwd, up)
    right /= np.linalg.norm(right)
    up = np.cross(right, fwd)
    view = np.eye(4)
    view[0, :3] = right
    view[1, :3] = up
    view[2, :3] = -fwd
    view[:3, 3] = -view[:3, :3] @ eye
    near, far = 0.01 * radius, 4.0 * radius
    proj = np.diag([1.0 / radius, 1.0 / radius, -2.0 / (far - near), 1.0])
    proj[2, 3] = -(far + near) / (far - near)
    return proj @ view


class Camera26Backend(BakeBackend):
    name = "cameras26"

    def bake(self, mesh: MeshData, old_uvs: np.ndarray,
             materials: list[MaterialData], opts: BakeOptions,
             log: PipelineLog, source: MeshData | None = None) -> BakeResult:
        import moderngl

        src = source if source is not None else mesh
        src_uvs = src.uvs if source is not None else old_uvs

        res = int(np.clip(opts.resolution, 64, 4096))
        cam_res = min(2048, res)
        lo = np.minimum(src.positions.min(axis=0), mesh.positions.min(axis=0))
        hi = np.maximum(src.positions.max(axis=0), mesh.positions.max(axis=0))
        center = (hi + lo) / 2.0
        radius = float(np.linalg.norm(hi - lo)) / 2.0
        radius = max(radius, 1e-6) * 1.05
        dirs = _camera_dirs()
        log.info(f"bake 26-camera: atlas {res}px, kamery {cam_res}px, "
                 f"zrodlo {len(src.indices)} tri -> cel {len(mesh.indices)} tri")

        ctx = moderngl.create_context(standalone=True)
        try:
            return self._bake_gl(ctx, mesh, src, src_uvs, materials, opts, res,
                                 cam_res, center.astype(np.float64), radius,
                                 dirs, log)
        finally:
            ctx.release()

    def _bake_gl(self, ctx, mesh, src, src_uvs, materials, opts, res, cam_res,
                 center, radius, dirs, log) -> BakeResult:
        import moderngl

        # ---- renders z 26 kamer ----
        prog = ctx.program(vertex_shader=CAM_VS, fragment_shader=CAM_FS)
        for i, name in enumerate(("uBaseColor", "uNormal", "uOrm",
                                  "uEmissive", "uOpacity")):
            prog[name] = i
        prog["uFlipInputG"] = bool(opts.input_normal_flip_g)

        src_tan = compute_tangents(src, src_uvs)
        vbo_data = np.hstack([
            src.positions.astype(np.float32),
            src_uvs.astype(np.float32),
            src.normals.astype(np.float32),
            src_tan.astype(np.float32),
        ]).astype(np.float32)
        vbo = ctx.buffer(vbo_data.tobytes())

        mat_texs = [material_gl_textures(ctx, m) for m in materials]
        ibos = []
        for mat_id in range(len(materials)):
            tri_mask = src.tri_material == mat_id
            sub = np.ascontiguousarray(src.indices[tri_mask], dtype=np.uint32)
            ibos.append(ctx.buffer(sub.tobytes()) if len(sub) else None)

        targets = [ctx.texture((cam_res, cam_res), 4) for _ in range(5)]
        depth_rb = ctx.depth_texture((cam_res, cam_res))
        fbo = ctx.framebuffer(color_attachments=targets,
                              depth_attachment=depth_rb)

        cam_maps = np.zeros((26, 5, cam_res, cam_res, 3), dtype=np.uint8)
        cam_depth = np.zeros((26, cam_res, cam_res), dtype=np.float32)
        cam_vp = np.zeros((26, 4, 4), dtype=np.float64)

        for ci, d in enumerate(dirs):
            vp = _ortho_viewproj(d, center, radius)
            cam_vp[ci] = vp
            fbo.use()
            ctx.viewport = (0, 0, cam_res, cam_res)
            ctx.enable(moderngl.DEPTH_TEST)
            ctx.disable(moderngl.CULL_FACE)
            fbo.clear(0.0, 0.0, 0.0, 0.0, depth=1.0)
            prog["uViewProj"].write(
                np.ascontiguousarray(vp.T, dtype=np.float32).tobytes())
            for mat_id, ibo in enumerate(ibos):
                if ibo is None:
                    continue
                vao = ctx.vertex_array(
                    prog, [(vbo, "3f 2f 3f 4f",
                            "in_pos", "in_old_uv", "in_normal", "in_old_tan")],
                    index_buffer=ibo, index_element_size=4)
                for slot, t in enumerate(mat_texs[mat_id]):
                    t.use(slot)
                vao.render(moderngl.TRIANGLES)
                vao.release()
            for mi, t in enumerate(targets):
                a = np.frombuffer(t.read(), dtype=np.uint8) \
                      .reshape(cam_res, cam_res, 4)
                cam_maps[ci, mi] = a[:, :, :3]           # GL bottom-up
            cam_depth[ci] = np.frombuffer(
                depth_rb.read(), dtype=np.float32).reshape(cam_res, cam_res)
            log.progress(35 + 10 * (ci + 1) / 26, f"kamera {ci + 1}/26")

        for obj in (fbo, depth_rb, *targets, vbo):
            obj.release()
        for ibo in ibos:
            if ibo is not None:
                ibo.release()
        for texs in mat_texs:
            for t in texs:
                t.release()

        # ---- G-buffer celu ----
        gbuf = render_gbuffer(ctx, mesh, res)
        cov = gbuf["coverage"]
        idx = np.argwhere(cov)
        pos = gbuf["pos"][cov].astype(np.float64)
        nrm = gbuf["normal"][cov].astype(np.float64)
        tan = gbuf["tangent"][cov].astype(np.float64)
        n_tex = len(idx)
        log.info(f"pokrycie atlasu: {cov.mean() * 100:.1f}% ({n_tex} texeli)")

        # ---- wybor kamery per texel (wektorowo po 26 kamerach) ----
        # kamera patrzy wzdluz -d, wiec powierzchnia frontem do niej
        # ma dot(normal, d) > 0
        scores = nrm @ dirs.T                          # (N,26)
        pos_h = np.concatenate([pos, np.ones((n_tex, 1))], axis=1)
        best_cam = np.full(n_tex, -1, dtype=np.int64)
        best_score = np.full(n_tex, 0.05)              # minimalny sensowny kat
        px_cache = np.zeros((26, n_tex, 2), dtype=np.int64)
        # bias rosnie przy malych renderach (gradient glebi w pikselu)
        bias = 0.003 + 1.5 / cam_res
        for ci in range(26):
            clip = pos_h @ cam_vp[ci].T                # ortho: w=1
            ndcx, ndcy, ndcz = clip[:, 0], clip[:, 1], clip[:, 2]
            x = np.clip(((ndcx * 0.5 + 0.5) * (cam_res - 1)).astype(np.int64),
                        0, cam_res - 1)
            yy = np.clip(((ndcy * 0.5 + 0.5) * (cam_res - 1)).astype(np.int64),
                         0, cam_res - 1)                # GL bottom-up wiersz
            px_cache[ci, :, 0] = yy
            px_cache[ci, :, 1] = x
            depth01 = ndcz * 0.5 + 0.5
            visible = depth01 <= cam_depth[ci, yy, x] + bias
            better = visible & (scores[:, ci] > best_score)
            best_cam[better] = ci
            best_score[better] = scores[better, ci]

        got = best_cam >= 0
        if (~got).any():
            # fallback: cel (zdecymowany LOD) moze odstawac od powierzchni
            # zrodla bardziej niz bias — bierz kamere o najlepszym kacie
            fb = ~got & (scores.max(axis=1) > 0.05)
            best_cam[fb] = scores[fb].argmax(axis=1)
            log.info(f"26-camera: {int(fb.sum())} texeli bez testu depth "
                     f"(fallback po kacie), {int((~got & ~fb).sum())} "
                     f"pustych — wypelni je dilation")
            got = best_cam >= 0

        out = {k: np.zeros((res, res, 3), dtype=np.uint8)
               for k in ("basecolor", "normal", "orm", "emissive", "opacity")}
        out["normal"][:, :] = (128, 128, 255)
        rows = idx[got]
        map_names = ["basecolor", "normal", "orm", "emissive", "opacity"]
        world_n = np.zeros((n_tex, 3), dtype=np.float64)
        for ci in range(26):
            sel = got & (best_cam == ci)
            if not sel.any():
                continue
            yy = px_cache[ci, sel, 0]
            x = px_cache[ci, sel, 1]
            rsel = idx[sel]
            for mi, key in enumerate(map_names):
                if key == "normal":
                    continue
                out[key][rsel[:, 0], rsel[:, 1]] = cam_maps[ci, mi, yy, x]
            world_n[sel] = cam_maps[ci, 1, yy, x].astype(np.float64) \
                / 255.0 * 2.0 - 1.0

        # normalki world -> tangent space celu
        if got.any():
            enc = encode_to_tangent_space(world_n[got], nrm[got], tan[got])
            out["normal"][rows[:, 0], rows[:, 1]] = enc

        any_emissive = any(m.has_emissive() for m in materials)
        any_opacity = any(m.has_opacity() for m in materials)
        eff_cov = np.zeros((res, res), dtype=bool)
        eff_cov[rows[:, 0], rows[:, 1]] = True
        images = {}
        for key in map_names:
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
