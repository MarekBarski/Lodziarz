"""Export GLB (pygltflib) — wszystkie LOD-y jako nody <Nazwa>_LOD0..N.

Viewer korzysta z tego pliku: przelaczanie LOD po nazwach nodow,
TEXCOORD_1 = stare UV (podglad "UV przed bake"), opacity w alfie base color.
LOD-y baked -> jeden primitive z materialem atlasowym; LOD-y z oryginalnymi
materialami -> primitive per material zrodlowy.
"""
from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import pygltflib as gl
from PIL import Image

from ..core import LodChain, MaterialData, MeshData
from ..logutil import PipelineLog


class _BufferBuilder:
    def __init__(self):
        self.blob = bytearray()
        self.views: list[gl.BufferView] = []
        self.accessors: list[gl.Accessor] = []

    def _add_view(self, data: bytes, target: int | None) -> int:
        while len(self.blob) % 4:
            self.blob.append(0)
        view = gl.BufferView(buffer=0, byteOffset=len(self.blob),
                             byteLength=len(data), target=target)
        self.blob.extend(data)
        self.views.append(view)
        return len(self.views) - 1

    def add_array(self, arr: np.ndarray, ctype: int, atype: str,
                  target: int, minmax: bool = False) -> int:
        arr = np.ascontiguousarray(arr)
        vi = self._add_view(arr.tobytes(), target)
        acc = gl.Accessor(
            bufferView=vi, componentType=ctype, count=len(arr), type=atype)
        if minmax:
            acc.min = [float(x) for x in arr.min(axis=0)]
            acc.max = [float(x) for x in arr.max(axis=0)]
        self.accessors.append(acc)
        return len(self.accessors) - 1

    def add_image(self, img: Image.Image) -> int:
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return self._add_view(buf.getvalue(), None)


def export_glb(chain: LodChain, out_path: Path, images: dict,
               log: PipelineLog) -> Path:
    g = gl.GLTF2(asset=gl.Asset(version="2.0", generator="lodziarz"))
    bb = _BufferBuilder()
    g.samplers = [gl.Sampler(magFilter=9729, minFilter=9987,
                             wrapS=10497, wrapT=10497)]

    baked = chain.baked if chain.baked is not None \
        else [chain.baked_material_index is not None] * len(chain.lods)

    baked_gltf: int | None = None
    if any(baked) and images:
        baked_gltf = _add_baked_material(g, bb, chain, images)
    # materialy zrodlowe — tylko te uzywane przez nie-baked LOD-y
    src_gltf: dict[int, int] = {}
    for i, lod in enumerate(chain.lods):
        if baked[i]:
            continue
        for mid in np.unique(lod.tri_material):
            mid = int(mid)
            if mid not in src_gltf and 0 <= mid < len(chain.materials):
                src_gltf[mid] = _add_source_material(g, bb, chain.materials[mid])

    g.scene = 0
    g.scenes = [gl.Scene(nodes=[])]
    for i, mesh in enumerate(chain.lods):
        name = f"{chain.asset_name}_LOD{i}"
        attributes = _mesh_attributes(bb, mesh)
        prims = []
        if baked[i]:
            idx = bb.add_array(mesh.indices.astype(np.uint32).reshape(-1, 1),
                               5125, "SCALAR", 34963)
            prims.append(gl.Primitive(attributes=attributes, indices=idx,
                                      material=baked_gltf))
        else:
            for mid in np.unique(mesh.tri_material):
                sub = mesh.indices[mesh.tri_material == int(mid)]
                idx = bb.add_array(sub.astype(np.uint32).reshape(-1, 1),
                                   5125, "SCALAR", 34963)
                prims.append(gl.Primitive(
                    attributes=attributes, indices=idx,
                    material=src_gltf.get(int(mid))))
        g.meshes.append(gl.Mesh(name=name, primitives=prims))
        g.nodes.append(gl.Node(name=name, mesh=len(g.meshes) - 1))
        g.scenes[0].nodes.append(len(g.nodes) - 1)

    g.bufferViews = bb.views
    g.accessors = bb.accessors
    g.buffers = [gl.Buffer(byteLength=len(bb.blob))]
    g.set_binary_blob(bytes(bb.blob))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    g.save_binary(str(out_path))
    log.info(f"GLB zapisany: {out_path.name}")
    return out_path


def _mesh_attributes(bb: _BufferBuilder, mesh: MeshData) -> gl.Attributes:
    # glTF: origin UV w lewym GORNYM rogu — nasze UV sa GL-owe (dolny), flip V
    uv0 = mesh.uvs.astype(np.float32).copy()
    uv0[:, 1] = 1.0 - uv0[:, 1]
    attributes = gl.Attributes(
        POSITION=bb.add_array(mesh.positions.astype(np.float32), 5126,
                              "VEC3", 34962, minmax=True),
        NORMAL=bb.add_array(mesh.normals.astype(np.float32), 5126,
                            "VEC3", 34962),
        TEXCOORD_0=bb.add_array(uv0, 5126, "VEC2", 34962),
    )
    if mesh.uvs2 is not None:
        uv1 = mesh.uvs2.astype(np.float32).copy()
        uv1[:, 1] = 1.0 - uv1[:, 1]
        attributes.TEXCOORD_1 = bb.add_array(uv1, 5126, "VEC2", 34962)
    return attributes


def _add_texture(g: gl.GLTF2, bb: _BufferBuilder, img: Image.Image) -> int:
    vi = bb.add_image(img)
    g.images.append(gl.Image(bufferView=vi, mimeType="image/png"))
    g.textures.append(gl.Texture(source=len(g.images) - 1, sampler=0))
    return len(g.textures) - 1


def _add_baked_material(g: gl.GLTF2, bb: _BufferBuilder, chain: LodChain,
                        images: dict) -> int:
    name = "M_baked"
    if chain.baked_material_index is not None \
            and chain.baked_material_index < len(chain.materials):
        name = chain.materials[chain.baked_material_index].name
    mat = gl.Material(name=name, pbrMetallicRoughness=gl.PbrMetallicRoughness())
    pbr = mat.pbrMetallicRoughness

    base = images.get("basecolor")
    opacity = images.get("opacity")
    if base is not None:
        rgba = base.convert("RGBA")
        if opacity is not None:
            rgba.putalpha(opacity.convert("L"))
            mat.alphaMode = "BLEND"
            mat.doubleSided = True
        pbr.baseColorTexture = gl.TextureInfo(index=_add_texture(g, bb, rgba))
    if images.get("normal") is not None:
        mat.normalTexture = gl.NormalMaterialTexture(
            index=_add_texture(g, bb, images["normal"]))
    if images.get("orm") is not None:
        # jeden plik ORM: occlusion czyta R, metallicRoughness czyta G/B
        ti = _add_texture(g, bb, images["orm"])
        pbr.metallicRoughnessTexture = gl.TextureInfo(index=ti)
        mat.occlusionTexture = gl.OcclusionTextureInfo(index=ti)
        pbr.metallicFactor = 1.0
        pbr.roughnessFactor = 1.0
    if images.get("emissive") is not None:
        mat.emissiveTexture = gl.TextureInfo(
            index=_add_texture(g, bb, images["emissive"]))
        mat.emissiveFactor = [1.0, 1.0, 1.0]
    g.materials.append(mat)
    return len(g.materials) - 1


def _add_source_material(g: gl.GLTF2, bb: _BufferBuilder,
                         src: MaterialData) -> int:
    mat = gl.Material(name=src.name,
                      pbrMetallicRoughness=gl.PbrMetallicRoughness())
    pbr = mat.pbrMetallicRoughness
    pbr.baseColorFactor = [float(c) for c in src.base_color_factor]
    if src.base_color_tex is not None:
        pbr.baseColorTexture = gl.TextureInfo(
            index=_add_texture(g, bb, src.base_color_tex))
    if src.normal_tex is not None:
        mat.normalTexture = gl.NormalMaterialTexture(
            index=_add_texture(g, bb, src.normal_tex))
    pbr.roughnessFactor = float(src.roughness_factor)
    pbr.metallicFactor = float(src.metallic_factor)
    if src.has_opacity():
        mat.alphaMode = "BLEND"
        mat.doubleSided = True
        pbr.baseColorFactor[3] = float(src.opacity_factor)
    g.materials.append(mat)
    return len(g.materials) - 1
