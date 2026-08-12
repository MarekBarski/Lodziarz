"""Wspolna infrastruktura backendow bake:

- kompozycja map materialu do numpy (tex * factory, ORM z komponentow)
- G-buffer w texel space (pozycja/normala/tangent world per texel atlasu)
- dilation, downsample wazony pokryciem, debug UV layout
- sampling nearest i re-enkodowanie normalek do tangent space celu
"""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw

from ..core import MaterialData, MeshData, compute_tangents

# ---------------------------------------------------------- kompozycja map


def srgb(x: float) -> float:
    return float(np.clip(x, 0.0, 1.0) ** (1.0 / 2.2))


def compose_constant(rgba: tuple, size: int = 4) -> np.ndarray:
    arr = np.zeros((size, size, 4), dtype=np.uint8)
    arr[:, :] = [int(np.clip(c, 0, 1) * 255 + 0.5) for c in rgba]
    return arr


def _channel(img, ch: int, size: tuple) -> np.ndarray | None:
    if img is None:
        return None
    a = np.asarray(img.convert("RGBA").resize(size, Image.BILINEAR),
                   dtype=np.float32)
    return a[:, :, ch] / 255.0


def compose_orm(mat: MaterialData) -> np.ndarray:
    sizes = [t.size for t in (mat.occlusion_tex, mat.roughness_tex,
                              mat.metallic_tex) if t is not None]
    if not sizes:
        return compose_constant((1.0, mat.roughness_factor,
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


def compose_material_arrays(mat: MaterialData) -> dict:
    """5 map materialu jako numpy uint8 HxWx4 (top-down, jak PIL)."""
    f = mat.base_color_factor
    if mat.base_color_tex is not None:
        bc = np.asarray(mat.base_color_tex.convert("RGBA"), dtype=np.float32)
        bc[:, :, :3] *= [f[0], f[1], f[2]]
        bc = np.clip(bc, 0, 255).astype(np.uint8)
    else:
        bc = compose_constant((srgb(f[0]), srgb(f[1]), srgb(f[2]), 1.0))

    if mat.normal_tex is not None:
        nm = np.asarray(mat.normal_tex.convert("RGBA"), dtype=np.uint8)
    else:
        nm = compose_constant((0.5, 0.5, 1.0, 1.0))

    orm = compose_orm(mat)

    if mat.emissive_tex is not None:
        em = np.asarray(mat.emissive_tex.convert("RGBA"), dtype=np.uint8)
    else:
        e = mat.emissive_factor
        em = compose_constant((srgb(e[0]), srgb(e[1]), srgb(e[2]), 1.0))

    if mat.opacity_tex is not None:
        op = np.asarray(mat.opacity_tex.convert("RGBA"), dtype=np.float32)
        op[:, :, :3] *= np.clip(mat.opacity_factor, 0.0, 1.0)
        op = np.clip(op, 0, 255).astype(np.uint8)
    else:
        fo = np.clip(mat.opacity_factor, 0.0, 1.0)
        op = compose_constant((fo, fo, fo, 1.0))

    return {"basecolor": bc, "normal": nm, "orm": orm,
            "emissive": em, "opacity": op}


def material_gl_textures(ctx, mat: MaterialData) -> list:
    """Tekstury GL w kolejnosci basecolor/normal/orm/emissive/opacity."""
    arrays = compose_material_arrays(mat)
    out = []
    for key in ("basecolor", "normal", "orm", "emissive", "opacity"):
        # GL: wiersz 0 na dole — nasze arraye sa top-down
        arr = np.ascontiguousarray(np.flipud(arrays[key]))
        t = ctx.texture((arr.shape[1], arr.shape[0]), 4, arr.tobytes())
        t.build_mipmaps()
        t.repeat_x = t.repeat_y = True
        out.append(t)
    return out


# ---------------------------------------------------------------- G-buffer

GBUF_VS = """
#version 330
in vec2 in_new_uv;
in vec3 in_pos;
in vec3 in_normal;
in vec4 in_tan;
out vec3 vPos;
out vec3 vN;
out vec4 vTan;
void main() {
    vPos = in_pos;
    vN = in_normal;
    vTan = in_tan;
    gl_Position = vec4(in_new_uv * 2.0 - 1.0, 0.0, 1.0);
}
"""

GBUF_FS = """
#version 330
in vec3 vPos;
in vec3 vN;
in vec4 vTan;
layout(location = 0) out vec4 outPos;
layout(location = 1) out vec4 outNormal;
layout(location = 2) out vec4 outTan;
void main() {
    outPos = vec4(vPos, 1.0);
    outNormal = vec4(normalize(vN), 1.0);
    outTan = vTan;
}
"""


def render_gbuffer(ctx, mesh: MeshData, res: int) -> dict:
    """Per texel atlasu (nowe UV): pozycja world, normala, tangent (nowe UV).

    Zwraca dict z arrayami top-down: pos (res,res,3) f32, normal, tangent
    (res,res,4), coverage (res,res) bool.
    """
    import moderngl

    new_tan = compute_tangents(mesh, mesh.uvs)
    prog = ctx.program(vertex_shader=GBUF_VS, fragment_shader=GBUF_FS)
    vbo_data = np.hstack([
        mesh.uvs.astype(np.float32),
        mesh.positions.astype(np.float32),
        mesh.normals.astype(np.float32),
        new_tan.astype(np.float32),
    ]).astype(np.float32)
    vbo = ctx.buffer(vbo_data.tobytes())
    ibo = ctx.buffer(np.ascontiguousarray(mesh.indices, dtype=np.uint32).tobytes())
    vao = ctx.vertex_array(
        prog, [(vbo, "2f 3f 3f 4f", "in_new_uv", "in_pos", "in_normal", "in_tan")],
        index_buffer=ibo, index_element_size=4)

    targets = [ctx.texture((res, res), 4, dtype="f4") for _ in range(3)]
    fbo = ctx.framebuffer(color_attachments=targets)
    fbo.use()
    ctx.viewport = (0, 0, res, res)
    ctx.disable(moderngl.DEPTH_TEST | moderngl.CULL_FACE)
    fbo.clear(0.0, 0.0, 0.0, 0.0)
    vao.render(moderngl.TRIANGLES)

    raw = [np.frombuffer(t.read(), dtype=np.float32).reshape(res, res, 4)
           for t in targets]
    for obj in (vao, ibo, vbo, fbo, *targets):
        obj.release()
    raw = [np.flipud(r).copy() for r in raw]
    return {
        "pos": raw[0][:, :, :3],
        "normal": raw[1][:, :, :3],
        "tangent": raw[2],
        "coverage": raw[0][:, :, 3] > 0.5,
    }


# ------------------------------------------------------------- postprocess


def dilate(rgb: np.ndarray, coverage: np.ndarray, iterations: int) -> np.ndarray:
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


def downsample_weighted(arr: np.ndarray, coverage: np.ndarray,
                        factor: int) -> np.ndarray:
    """Box filter wazony pokryciem — puste texele nie sciemniaja brzegow."""
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


def draw_uv_layout(mesh: MeshData, res: int) -> Image.Image:
    img = Image.new("RGB", (res, res), (12, 12, 12))
    d = ImageDraw.Draw(img)
    uv = mesh.uvs
    px = np.stack([uv[:, 0] * (res - 1), (1.0 - uv[:, 1]) * (res - 1)], axis=1)
    for tri in mesh.indices:
        a, b, c = px[tri[0]], px[tri[1]], px[tri[2]]
        d.line([tuple(a), tuple(b), tuple(c), tuple(a)],
               fill=(0, 200, 90), width=1)
    return img


# ---------------------------------------------------------------- sampling


def sample_nearest(img: np.ndarray, uv: np.ndarray) -> np.ndarray:
    """img: (H,W,C) top-down; uv: (N,2) w konwencji GL (v=0 na dole), wrap."""
    h, w = img.shape[:2]
    u = np.mod(uv[:, 0], 1.0)
    v = np.mod(uv[:, 1], 1.0)
    x = np.clip((u * w).astype(np.int64), 0, w - 1)
    y = np.clip(((1.0 - v) * h).astype(np.int64), 0, h - 1)
    return img[y, x]


def encode_to_tangent_space(n_world: np.ndarray, t_normal: np.ndarray,
                            t_tangent: np.ndarray) -> np.ndarray:
    """world normal -> tangent space celu; zwraca uint8 RGB (N,3)."""
    n = t_normal / np.maximum(np.linalg.norm(t_normal, axis=1, keepdims=True), 1e-12)
    t = t_tangent[:, :3] - n * np.sum(n * t_tangent[:, :3], axis=1, keepdims=True)
    tl = np.linalg.norm(t, axis=1, keepdims=True)
    bad = (tl < 1e-6)[:, 0]
    t = np.where(tl > 1e-6, t / np.maximum(tl, 1e-12), 0.0)
    if bad.any():
        t[bad] = [1.0, 0.0, 0.0]
    b = np.cross(n, t) * t_tangent[:, 3:4]
    nw = n_world / np.maximum(np.linalg.norm(n_world, axis=1, keepdims=True), 1e-12)
    ts = np.stack([np.sum(nw * t, axis=1),
                   np.sum(nw * b, axis=1),
                   np.sum(nw * n, axis=1)], axis=1)
    ts /= np.maximum(np.linalg.norm(ts, axis=1, keepdims=True), 1e-12)
    return (np.clip(ts * 0.5 + 0.5, 0, 1) * 255 + 0.5).astype(np.uint8)


def decode_normal_map(rgb: np.ndarray, flip_g: bool) -> np.ndarray:
    """uint8 RGB -> wektory tangent-space (N,3) f32."""
    v = rgb[:, :3].astype(np.float32) / 255.0 * 2.0 - 1.0
    if flip_g:
        v[:, 1] = -v[:, 1]
    return v


def tbn_to_world(n_ts: np.ndarray, normal: np.ndarray,
                 tangent4: np.ndarray) -> np.ndarray:
    """wektory tangent-space -> world przy zadanym TBN (N,3)/(N,4)."""
    n = normal / np.maximum(np.linalg.norm(normal, axis=1, keepdims=True), 1e-12)
    t = tangent4[:, :3] - n * np.sum(n * tangent4[:, :3], axis=1, keepdims=True)
    tl = np.linalg.norm(t, axis=1, keepdims=True)
    bad = (tl < 1e-6)[:, 0]
    t = np.where(tl > 1e-6, t / np.maximum(tl, 1e-12), 0.0)
    if bad.any():
        t[bad] = [1.0, 0.0, 0.0]
    b = np.cross(n, t) * tangent4[:, 3:4]
    w = (t * n_ts[:, 0:1] + b * n_ts[:, 1:2] + n * n_ts[:, 2:3])
    return w / np.maximum(np.linalg.norm(w, axis=1, keepdims=True), 1e-12)
