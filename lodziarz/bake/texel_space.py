"""Texel-space bake (default): rasteryzacja nowego UV layoutu na GPU,
per texel odczyt starego UV i sampling skomponowanych tekstur materialu.

Normalki sa poprawnie re-enkodowane: stary tangent space -> world ->
nowy tangent space (tangenty liczone dla obu zestawow UV).
"""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw

from . import BakeBackend, BakeOptions, BakeResult
from ..core import MaterialData, MeshData, compute_tangents
from ..logutil import PipelineLog

VS = """
#version 330
in vec2 in_new_uv;
in vec2 in_old_uv;
in vec3 in_normal;
in vec4 in_old_tan;
in vec4 in_new_tan;
out vec2 vOldUv;
out vec3 vN;
out vec4 vTo;
out vec4 vTn;
void main() {
    vOldUv = in_old_uv;
    vN = in_normal;
    vTo = in_old_tan;
    vTn = in_new_tan;
    gl_Position = vec4(in_new_uv * 2.0 - 1.0, 0.0, 1.0);
}
"""

FS = """
#version 330
uniform sampler2D uBaseColor;
uniform sampler2D uNormal;
uniform sampler2D uOrm;
uniform sampler2D uEmissive;
uniform sampler2D uOpacity;
uniform bool uFlipInputG;
in vec2 vOldUv;
in vec3 vN;
in vec4 vTo;
in vec4 vTn;
layout(location = 0) out vec4 outBaseColor;
layout(location = 1) out vec4 outNormal;
layout(location = 2) out vec4 outOrm;
layout(location = 3) out vec4 outEmissive;
layout(location = 4) out vec4 outOpacity;
void main() {
    outBaseColor = vec4(texture(uBaseColor, vOldUv).rgb, 1.0);
    outOrm = vec4(texture(uOrm, vOldUv).rgb, 1.0);
    outEmissive = vec4(texture(uEmissive, vOldUv).rgb, 1.0);
    outOpacity = vec4(texture(uOpacity, vOldUv).rgb, 1.0);

    vec3 nts = texture(uNormal, vOldUv).xyz * 2.0 - 1.0;
    if (uFlipInputG) nts.y = -nts.y;
    vec3 N = normalize(vN);
    vec3 To = vTo.xyz - N * dot(N, vTo.xyz);
    To = length(To) > 1e-6 ? normalize(To) : vec3(1.0, 0.0, 0.0);
    vec3 Bo = cross(N, To) * vTo.w;
    vec3 nws = normalize(To * nts.x + Bo * nts.y + N * nts.z);
    vec3 Tn = vTn.xyz - N * dot(N, vTn.xyz);
    Tn = length(Tn) > 1e-6 ? normalize(Tn) : vec3(1.0, 0.0, 0.0);
    vec3 Bn = cross(N, Tn) * vTn.w;
    vec3 outN = normalize(vec3(dot(nws, Tn), dot(nws, Bn), dot(nws, N)));
    outNormal = vec4(outN * 0.5 + 0.5, 1.0);
}
"""


class TexelSpaceBackend(BakeBackend):
    name = "texel"

    def bake(self, mesh: MeshData, old_uvs: np.ndarray,
             materials: list[MaterialData], opts: BakeOptions,
             log: PipelineLog) -> BakeResult:
        import moderngl

        res = int(np.clip(opts.resolution, 64, 8192))
        ssaa = max(1, int(opts.ssaa))
        rres = min(res * ssaa, 8192)
        log.info(f"bake texel-space: {res}px (raster {rres}px), "
                 f"dilation {opts.dilation}px, materialy: {len(materials)}")

        old_tan = compute_tangents(mesh, old_uvs)
        new_tan = compute_tangents(mesh, mesh.uvs)

        ctx = moderngl.create_context(standalone=True)
        try:
            return self._bake_gl(ctx, mesh, old_uvs, old_tan, new_tan,
                                 materials, opts, res, rres, log)
        finally:
            ctx.release()

    def _bake_gl(self, ctx, mesh, old_uvs, old_tan, new_tan,
                 materials, opts, res, rres, log) -> BakeResult:
        import moderngl

        prog = ctx.program(vertex_shader=VS, fragment_shader=FS)
        prog["uBaseColor"] = 0
        prog["uNormal"] = 1
        prog["uOrm"] = 2
        prog["uEmissive"] = 3
        prog["uOpacity"] = 4
        prog["uFlipInputG"] = bool(opts.input_normal_flip_g)

        vbo_data = np.hstack([
            mesh.uvs.astype(np.float32),
            old_uvs.astype(np.float32),
            mesh.normals.astype(np.float32),
            old_tan.astype(np.float32),
            new_tan.astype(np.float32),
        ]).astype(np.float32)
        vbo = ctx.buffer(vbo_data.tobytes())

        targets = [ctx.texture((rres, rres), 4) for _ in range(5)]
        fbo = ctx.framebuffer(color_attachments=targets)
        fbo.use()
        ctx.viewport = (0, 0, rres, rres)
        ctx.disable(moderngl.DEPTH_TEST | moderngl.CULL_FACE)
        fbo.clear(0.0, 0.0, 0.0, 0.0)

        # jeden draw per material — bind skomponowanych tekstur tego materialu
        any_emissive = any(m.has_emissive() for m in materials)
        any_opacity = any(m.has_opacity() for m in materials)
        for mat_id, mat in enumerate(materials):
            tri_mask = mesh.tri_material == mat_id
            if not tri_mask.any():
                continue
            sub = np.ascontiguousarray(mesh.indices[tri_mask], dtype=np.uint32)
            ibo = ctx.buffer(sub.tobytes())
            vao = ctx.vertex_array(
                prog,
                [(vbo, "2f 2f 3f 4f 4f",
                  "in_new_uv", "in_old_uv", "in_normal",
                  "in_old_tan", "in_new_tan")],
                index_buffer=ibo, index_element_size=4)
            texs = _material_gl_textures(ctx, mat)
            for slot, t in enumerate(texs):
                t.use(slot)
            vao.render(moderngl.TRIANGLES)
            vao.release()
            ibo.release()
            for t in texs:
                t.release()

        # readback
        raw = [np.frombuffer(t.read(), dtype=np.uint8).reshape(rres, rres, 4)
               for t in targets]
        fbo.release()
        for t in targets:
            t.release()
        vbo.release()

        # texture space: GL row 0 = dol; obrazy trzymamy top-down
        raw = [np.flipud(r) for r in raw]
        coverage = raw[0][:, :, 3] > 0

        if rres != res:
            raw = [_downsample(r, coverage, rres // res) for r in raw]
            coverage = raw[0][:, :, 3] > 0

        log.info(f"pokrycie atlasu: {coverage.mean() * 100:.1f}% texeli")
        images = {}
        names = ["basecolor", "normal", "orm", "emissive", "opacity"]
        for i, name in enumerate(names):
            if name == "emissive" and not any_emissive:
                continue
            if name == "opacity" and not any_opacity:
                continue
            img = _dilate(raw[i][:, :, :3], coverage, opts.dilation)
            if name == "opacity":
                images[name] = Image.fromarray(img[:, :, 0], "L")
            else:
                images[name] = Image.fromarray(img, "RGB")

        cov_img = Image.fromarray((coverage * 255).astype(np.uint8), "L")
        uv_img = _draw_uv_layout(mesh, res)
        return BakeResult(images=images, coverage=cov_img, uv_layout=uv_img)


def _compose_constant(rgba: tuple, size: int = 4) -> np.ndarray:
    arr = np.zeros((size, size, 4), dtype=np.uint8)
    arr[:, :] = [int(np.clip(c, 0, 1) * 255 + 0.5) for c in rgba]
    return arr


def _srgb(x: float) -> float:
    return float(np.clip(x, 0.0, 1.0) ** (1.0 / 2.2))


def _material_gl_textures(ctx, mat: MaterialData) -> list:
    """Komponuje 4 tekstury materialu (CPU) i laduje na GPU.

    basecolor = tex * factor; orm = O(tex R) / R(tex G lub gray * factor) /
    M(tex B lub gray * factor); normal = tex albo flat; emissive = tex albo factor.
    """
    f = mat.base_color_factor
    if mat.base_color_tex is not None:
        bc = np.asarray(mat.base_color_tex.convert("RGBA"), dtype=np.float32)
        bc[:, :, :3] *= [f[0], f[1], f[2]]
        bc = np.clip(bc, 0, 255).astype(np.uint8)
    else:
        bc = _compose_constant((_srgb(f[0]), _srgb(f[1]), _srgb(f[2]), 1.0))

    if mat.normal_tex is not None:
        nm = np.asarray(mat.normal_tex.convert("RGBA"), dtype=np.uint8)
    else:
        nm = _compose_constant((0.5, 0.5, 1.0, 1.0))

    orm = _compose_orm(mat)

    if mat.emissive_tex is not None:
        em = np.asarray(mat.emissive_tex.convert("RGBA"), dtype=np.uint8)
    else:
        e = mat.emissive_factor
        em = _compose_constant((_srgb(e[0]), _srgb(e[1]), _srgb(e[2]), 1.0))

    if mat.opacity_tex is not None:
        op = np.asarray(mat.opacity_tex.convert("RGBA"), dtype=np.float32)
        op[:, :, :3] *= np.clip(mat.opacity_factor, 0.0, 1.0)
        op = np.clip(op, 0, 255).astype(np.uint8)
    else:
        f = np.clip(mat.opacity_factor, 0.0, 1.0)
        op = _compose_constant((f, f, f, 1.0))

    out = []
    for arr in (bc, nm, orm, em, op):
        # GL: wiersz 0 na dole — obrazy PIL sa top-down
        arr = np.ascontiguousarray(np.flipud(arr))
        t = ctx.texture((arr.shape[1], arr.shape[0]), 4, arr.tobytes())
        t.build_mipmaps()
        t.repeat_x = t.repeat_y = True
        out.append(t)
    return out


def _channel(img: Image.Image | None, ch: int, size: tuple) -> np.ndarray | None:
    if img is None:
        return None
    a = np.asarray(img.convert("RGBA").resize(size, Image.BILINEAR),
                   dtype=np.float32)
    return a[:, :, ch] / 255.0


def _compose_orm(mat: MaterialData) -> np.ndarray:
    sizes = [t.size for t in (mat.occlusion_tex, mat.roughness_tex,
                              mat.metallic_tex) if t is not None]
    if not sizes:
        return _compose_constant((1.0, mat.roughness_factor,
                                  mat.metallic_factor, 1.0))
    size = (max(s[0] for s in sizes), max(s[1] for s in sizes))
    h, w = size[1], size[0]

    packed_mr = mat.roughness_tex is not None and mat.roughness_tex is mat.metallic_tex
    occ = _channel(mat.occlusion_tex, 0, size)
    if packed_mr:
        rough = _channel(mat.roughness_tex, 1, size)
        metal = _channel(mat.metallic_tex, 2, size)
    else:
        rough = _channel(mat.roughness_tex, 0, size)   # standalone gray
        metal = _channel(mat.metallic_tex, 0, size)
    orm = np.zeros((h, w, 4), dtype=np.float32)
    orm[:, :, 0] = occ if occ is not None else 1.0
    orm[:, :, 1] = (rough if rough is not None else 1.0) * mat.roughness_factor
    orm[:, :, 2] = (metal if metal is not None else 1.0) * mat.metallic_factor
    orm[:, :, 3] = 1.0
    return (np.clip(orm, 0, 1) * 255 + 0.5).astype(np.uint8)


def _downsample(arr: np.ndarray, coverage: np.ndarray, factor: int) -> np.ndarray:
    """Box filter wazony pokryciem — puste texele nie sciemniaja brzegow wysp."""
    h, w, _ = arr.shape
    a = arr.astype(np.float32).reshape(h // factor, factor,
                                       w // factor, factor, 4)
    cov = coverage.astype(np.float32).reshape(h // factor, factor,
                                              w // factor, factor)
    wsum = cov.sum(axis=(1, 3))
    weighted = (a * cov[:, :, :, :, None]).sum(axis=(1, 3))
    out = np.zeros_like(weighted)
    mask = wsum > 0
    out[mask] = weighted[mask] / wsum[mask][:, None]
    out[:, :, 3] = np.where(mask, 255.0, 0.0)
    return (out + 0.5).astype(np.uint8)


def _dilate(rgb: np.ndarray, coverage: np.ndarray, iterations: int) -> np.ndarray:
    """Iteracyjne rozlewanie brzegow wysp na puste texele (padding)."""
    img = rgb.astype(np.float32)
    valid = coverage.copy()
    shifts = [(-1, -1), (-1, 0), (-1, 1), (0, -1),
              (0, 1), (1, -1), (1, 0), (1, 1)]
    for _ in range(max(0, iterations)):
        if valid.all():
            break
        acc = np.zeros_like(img)
        cnt = np.zeros(valid.shape, dtype=np.float32)
        for dy, dx in shifts:
            v = np.roll(valid, (dy, dx), axis=(0, 1))
            p = np.roll(img, (dy, dx), axis=(0, 1))
            acc += p * v[:, :, None]
            cnt += v
        fill = (~valid) & (cnt > 0)
        img[fill] = acc[fill] / cnt[fill][:, None]
        valid = valid | fill
    return (img + 0.5).astype(np.uint8)


def _draw_uv_layout(mesh: MeshData, res: int) -> Image.Image:
    img = Image.new("RGB", (res, res), (12, 12, 12))
    d = ImageDraw.Draw(img)
    uv = mesh.uvs
    px = np.stack([uv[:, 0] * (res - 1), (1.0 - uv[:, 1]) * (res - 1)], axis=1)
    for tri in mesh.indices:
        a, b, c = px[tri[0]], px[tri[1]], px[tri[2]]
        d.line([tuple(a), tuple(b), tuple(c), tuple(a)],
               fill=(0, 200, 90), width=1)
    return img
