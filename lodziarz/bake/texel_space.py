"""Texel-space bake (default): rasteryzacja nowego UV layoutu na GPU,
per texel odczyt starego UV i sampling skomponowanych tekstur materialu.

Normalki sa poprawnie re-enkodowane: stary tangent space -> world ->
nowy tangent space (tangenty liczone dla obu zestawow UV).
"""
from __future__ import annotations

import numpy as np
from PIL import Image

from . import BakeBackend, BakeOptions, BakeResult
from .common import (dilate, downsample_weighted, draw_uv_layout,
                     material_gl_textures)
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
             log: PipelineLog, source: MeshData | None = None) -> BakeResult:
        # texel-space sampluje po starych UV celu — poprawny tylko gdy cel
        # ma topologie zrodla (unwrap LOD0); source jest ignorowany
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
            texs = material_gl_textures(ctx, mat)
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
            raw = [downsample_weighted(r, coverage, rres // res) for r in raw]
            coverage = raw[0][:, :, 3] > 0

        log.info(f"pokrycie atlasu: {coverage.mean() * 100:.1f}% texeli")
        images = {}
        names = ["basecolor", "normal", "orm", "emissive", "opacity"]
        for i, name in enumerate(names):
            if name == "emissive" and not any_emissive:
                continue
            if name == "opacity" and not any_opacity:
                continue
            img = dilate(raw[i][:, :, :3], coverage, opts.dilation)
            if name == "opacity":
                images[name] = Image.fromarray(img[:, :, 0], "L")
            else:
                images[name] = Image.fromarray(img, "RGB")

        cov_img = Image.fromarray((coverage * 255).astype(np.uint8), "L")
        uv_img = draw_uv_layout(mesh, res)
        return BakeResult(images=images, coverage=cov_img, uv_layout=uv_img)
