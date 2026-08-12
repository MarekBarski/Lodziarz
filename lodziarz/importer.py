"""Import FBX (ufbx) oraz OBJ / glTF / GLB (trimesh) do wspolnego modelu Asset."""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from .core import Asset, MaterialData, MeshData, compute_smooth_normals, weld_vertices
from .logutil import PipelineLog

SUPPORTED = {".fbx", ".obj", ".gltf", ".glb"}


def load_asset(path: str | Path, log: PipelineLog) -> Asset:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"plik nie istnieje: {p}")
    ext = p.suffix.lower()
    if ext not in SUPPORTED:
        raise ValueError(f"nieobslugiwany format '{ext}' (FBX/OBJ/glTF/GLB)")
    log.info(f"import: {p.name}")
    if ext == ".fbx":
        asset = _load_fbx(p, log)
    else:
        asset = _load_trimesh(p, log)
    asset.mesh.validate()
    if asset.mesh.triangle_count == 0:
        raise ValueError("zaimportowano 0 trojkatow — plik pusty albo sama hierarchia")
    log.info(f"zaimportowano: {asset.mesh.triangle_count} tri, "
             f"{asset.mesh.vertex_count} verts, {len(asset.materials)} materialow")
    return asset


# ---------------------------------------------------------------- FBX (ufbx)

def _load_fbx(path: Path, log: PipelineLog) -> Asset:
    import ufbx
    try:
        scene = ufbx.load_file(str(path))
    except Exception as e:
        raise ValueError(f"ufbx nie potrafi otworzyc pliku: {e}") from e

    unit = getattr(scene.settings, "unit_meters", 0.01) or 0.01

    # mapowanie material -> globalny indeks
    materials: list[MaterialData] = []
    mat_index: dict[int, int] = {}

    def material_id(mat) -> int:
        if mat is None:
            return _default_material(materials)
        key = mat.element_id
        if key not in mat_index:
            mat_index[key] = len(materials)
            materials.append(_convert_ufbx_material(mat, path.parent, log))
        return mat_index[key]

    all_pos, all_nrm, all_uv, all_mat = [], [], [], []

    # UWAGA na ufbx 0.0.5: node.parent/node.children psuja pamiec, po
    # scene.nodes wolno iterowac tylko RAZ, a glebszy dostep do danych
    # w trakcie iteracji unieważnia iterator (access violation). Dlatego:
    # (1) jedna plytka iteracja zbiera referencje nodow,
    # (2) ekstrakcja idzie po zwyklej liscie Pythona.
    import re
    collected = []
    for node in scene.nodes:
        collected.append((node.name or "", int(node.attrib_type), node))

    # pomijamy LOD-y > 0 gdy plik ma LODGroup (attrib_type 16): skip po
    # konwencji nazw <Grupa>_LOD<n> (UE/Brutgen/nasz writer)
    lod_groups = {name for name, at, _ in collected if at == 16}
    skipped_lod = 0
    for name, at, node in collected:
        mesh = node.mesh
        if mesh is None:
            continue
        m = re.match(r"^(.+)_LOD(\d+)$", name)
        if m and m.group(1) in lod_groups and int(m.group(2)) > 0:
            skipped_lod += 1
            continue
        try:
            pos, nrm, uv, mat = _extract_ufbx_mesh(node, mesh, unit, material_id)
        except Exception as e:
            log.warn(f"pominieto mesh '{mesh.name or node.name}': {e}")
            continue
        if len(pos):
            all_pos.append(pos); all_nrm.append(nrm)
            all_uv.append(uv); all_mat.append(mat)
    if skipped_lod:
        log.info(f"pominieto {skipped_lod} nizszych LOD-ow ze zrodlowego LODGroup")

    # jawne zwolnienie sceny PO zrzuceniu wrapperow — destruktor wywolany
    # przez GC na scenie z zyjacymi wrapperami crashuje (ufbx 0.0.5)
    collected.clear()
    try:
        del node, mesh
    except NameError:
        pass
    import gc
    gc.collect()
    scene.free()

    if not all_pos:
        raise ValueError("plik FBX nie zawiera geometrii")

    mesh = weld_vertices(
        np.vstack(all_pos), np.vstack(all_nrm), np.vstack(all_uv),
        np.concatenate(all_mat))
    if not materials:
        materials.append(MaterialData(name="Default"))
    return Asset(name=path.stem, mesh=mesh, materials=materials,
                 source_path=str(path))


def _extract_ufbx_mesh(node, mesh, unit: float, material_id) -> tuple:
    """Rozwija mesh ufbx do per-corner arrays w world space (metry)."""
    m = node.node_to_world  # ufbx.Matrix — kolumny c0..c3
    cols = [m.c0, m.c1, m.c2, m.c3]
    world = np.array([[cols[0].x, cols[1].x, cols[2].x, cols[3].x],
                      [cols[0].y, cols[1].y, cols[2].y, cols[3].y],
                      [cols[0].z, cols[1].z, cols[2].z, cols[3].z]],
                     dtype=np.float64)

    vp, vn, vu = mesh.vertex_position, mesh.vertex_normal, mesh.vertex_uv
    pos_vals = np.array([(v.x, v.y, v.z) for v in vp.values], dtype=np.float64)
    pos_idx = np.array(list(vp.indices), dtype=np.int64)
    if vn and vn.exists:
        nrm_vals = np.array([(v.x, v.y, v.z) for v in vn.values], dtype=np.float64)
        nrm_idx = np.array(list(vn.indices), dtype=np.int64)
    else:
        nrm_vals = nrm_idx = None
    if vu and vu.exists:
        uv_vals = np.array([(v.x, v.y) for v in vu.values], dtype=np.float64)
        uv_idx = np.array(list(vu.indices), dtype=np.int64)
    else:
        uv_vals = uv_idx = None

    face_mat_raw = list(mesh.face_material)
    mesh_mats = list(mesh.materials)
    # id materialu per lokalny slot
    slot_to_global = [material_id(mm) for mm in mesh_mats] or [material_id(None)]

    corners, tri_mats = [], []
    for fi in range(mesh.num_faces):
        face = mesh.faces[fi]
        n = face.num_indices
        if n < 3:
            continue
        base = face.index_begin
        slot = face_mat_raw[fi] if fi < len(face_mat_raw) else 0
        gmat = slot_to_global[slot if 0 <= slot < len(slot_to_global) else 0]
        for k in range(1, n - 1):  # fan triangulation
            corners.extend((base, base + k, base + k + 1))
            tri_mats.append(gmat)
    if not corners:
        return np.zeros((0, 3)), np.zeros((0, 3)), np.zeros((0, 2)), np.zeros(0)

    ci = np.array(corners, dtype=np.int64)
    p_local = pos_vals[pos_idx[ci]]
    p_world = (p_local @ world[:, :3].T + world[:, 3]) * unit
    if nrm_vals is not None:
        nrm = nrm_vals[nrm_idx[ci]]
        rot = world[:, :3]
        # inverse-transpose w formie wierszowej: n' = n @ inv(rot)
        nrm = nrm @ np.linalg.inv(rot) if abs(np.linalg.det(rot)) > 1e-12 else nrm
        ln = np.linalg.norm(nrm, axis=1, keepdims=True)
        ln[ln < 1e-12] = 1.0
        nrm = nrm / ln
    else:
        nrm = None
    uv = uv_vals[uv_idx[ci]] if uv_vals is not None else np.zeros((len(ci), 2))

    if nrm is None:
        # policz z geometrii per trojkat (flat) — weld je potem sklei
        tri = p_world.reshape(-1, 3, 3)
        fn = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
        ln = np.linalg.norm(fn, axis=1, keepdims=True)
        ln[ln < 1e-12] = 1.0
        nrm = np.repeat(fn / ln, 3, axis=0)

    return (p_world.astype(np.float32), nrm.astype(np.float32),
            uv.astype(np.float32), np.array(tri_mats, dtype=np.int32))


def _default_material(materials: list[MaterialData]) -> int:
    for i, m in enumerate(materials):
        if m.name == "__default__":
            return i
    materials.append(MaterialData(name="__default__"))
    return len(materials) - 1


def _tex_image(texture, base_dir: Path, log: PipelineLog) -> Image.Image | None:
    if texture is None:
        return None
    candidates = []
    for attr in ("absolute_filename", "filename", "relative_filename"):
        v = getattr(texture, attr, "") or ""
        if v:
            candidates.append(v)
    for c in candidates:
        for p in (Path(c), base_dir / Path(c).name, base_dir / c):
            if p.exists():
                try:
                    return Image.open(p).convert("RGBA")
                except Exception as e:
                    log.warn(f"tekstura {p.name}: {e}")
    # tekstura wbudowana w FBX
    content = getattr(texture, "content", None)
    if content:
        import io
        try:
            return Image.open(io.BytesIO(bytes(content))).convert("RGBA")
        except Exception as e:
            log.warn(f"embedded tekstura: {e}")
    if candidates:
        log.warn(f"nie znaleziono pliku tekstury: {candidates[0]}")
    return None


def _convert_ufbx_material(mat, base_dir: Path, log: PipelineLog) -> MaterialData:
    out = MaterialData(name=mat.name or "Material")
    pbr = mat.pbr
    bc = pbr.base_color
    if bc.has_value:
        v = bc.value_vec4
        out.base_color_factor = (v.x, v.y, v.z, v.w)
    out.base_color_tex = _tex_image(bc.texture, base_dir, log)
    out.normal_tex = _tex_image(pbr.normal_map.texture, base_dir, log)
    r, m_ = pbr.roughness, pbr.metalness
    if r.has_value:
        out.roughness_factor = float(r.value_vec4.x)
    if m_.has_value:
        out.metallic_factor = float(m_.value_vec4.x)
    out.roughness_tex = _tex_image(r.texture, base_dir, log)
    out.metallic_tex = _tex_image(m_.texture, base_dir, log)
    out.occlusion_tex = _tex_image(pbr.ambient_occlusion.texture, base_dir, log)
    em = pbr.emission_color
    out.emissive_tex = _tex_image(em.texture, base_dir, log)
    if em.has_value:
        f = pbr.emission_factor
        scale = float(f.value_vec4.x) if f.has_value else 1.0
        v = em.value_vec4
        out.emissive_factor = (v.x * scale, v.y * scale, v.z * scale)
    op = pbr.opacity
    out.opacity_tex = _tex_image(op.texture, base_dir, log)
    if op.has_value:
        out.opacity_factor = float(op.value_vec4.x)
    return out


# ------------------------------------------------- OBJ / glTF / GLB (trimesh)

def _load_trimesh(path: Path, log: PipelineLog) -> Asset:
    import trimesh
    try:
        loaded = trimesh.load(str(path), force="scene", process=False)
    except Exception as e:
        raise ValueError(f"trimesh nie potrafi otworzyc pliku: {e}") from e

    materials: list[MaterialData] = []
    all_pos, all_nrm, all_uv, all_mat = [], [], [], []

    for node_name in loaded.graph.nodes_geometry:
        transform, geom_name = loaded.graph[node_name]
        geom = loaded.geometry.get(geom_name)
        if geom is None or not isinstance(geom, trimesh.Trimesh):
            continue
        if geom.faces is None or len(geom.faces) == 0:
            log.warn(f"pominieto '{geom_name}': brak trojkatow")
            continue
        mat_id = len(materials)
        materials.append(_convert_trimesh_material(geom, geom_name, log))

        verts = np.asarray(geom.vertices, dtype=np.float64)
        verts = verts @ np.asarray(transform)[:3, :3].T + np.asarray(transform)[:3, 3]
        faces = np.asarray(geom.faces, dtype=np.int64)
        if geom.vertex_normals is not None and len(geom.vertex_normals) == len(verts):
            nrm = np.asarray(geom.vertex_normals, dtype=np.float64)
            rot = np.asarray(transform)[:3, :3]
            if abs(np.linalg.det(rot)) > 1e-12:
                nrm = nrm @ np.linalg.inv(rot)
                ln = np.linalg.norm(nrm, axis=1, keepdims=True)
                ln[ln < 1e-12] = 1.0
                nrm /= ln
        else:
            nrm = compute_smooth_normals(verts.astype(np.float32),
                                         faces.astype(np.uint32)).astype(np.float64)
        uv = None
        visual = getattr(geom, "visual", None)
        if visual is not None and getattr(visual, "uv", None) is not None \
                and len(visual.uv) == len(verts):
            uv = np.asarray(visual.uv, dtype=np.float64)
        if uv is None:
            log.warn(f"'{geom_name}': brak UV — bake bedzie wymagal unwrapu (i tak robimy)")
            uv = np.zeros((len(verts), 2), dtype=np.float64)

        all_pos.append(verts[faces].reshape(-1, 3).astype(np.float32))
        all_nrm.append(nrm[faces].reshape(-1, 3).astype(np.float32))
        all_uv.append(uv[faces].reshape(-1, 2).astype(np.float32))
        all_mat.append(np.full(len(faces), mat_id, dtype=np.int32))

    if not all_pos:
        raise ValueError("plik nie zawiera geometrii trojkatowej")

    mesh = weld_vertices(np.vstack(all_pos), np.vstack(all_nrm),
                         np.vstack(all_uv), np.concatenate(all_mat))
    if not materials:
        materials.append(MaterialData(name="Default"))
    return Asset(name=path.stem, mesh=mesh, materials=materials,
                 source_path=str(path))


def _to_rgba(img) -> Image.Image | None:
    if img is None:
        return None
    if isinstance(img, Image.Image):
        return img.convert("RGBA")
    return None


def _convert_trimesh_material(geom, name: str, log: PipelineLog) -> MaterialData:
    out = MaterialData(name=name)
    visual = getattr(geom, "visual", None)
    mat = getattr(visual, "material", None)
    if mat is None:
        return out
    if getattr(mat, "name", None):
        out.name = mat.name
    kind = type(mat).__name__
    if kind == "PBRMaterial":
        out.base_color_tex = _to_rgba(getattr(mat, "baseColorTexture", None))
        f = getattr(mat, "baseColorFactor", None)
        if f is not None:
            f = np.asarray(f, dtype=np.float64)
            if f.max() > 1.0:
                f = f / 255.0
            out.base_color_factor = tuple(float(x) for x in f[:4]) \
                if len(f) >= 4 else (*[float(x) for x in f[:3]], 1.0)
        out.normal_tex = _to_rgba(getattr(mat, "normalTexture", None))
        mr = _to_rgba(getattr(mat, "metallicRoughnessTexture", None))
        if mr is not None:
            out.roughness_tex = mr   # kanal G
            out.metallic_tex = mr    # kanal B
        out.occlusion_tex = _to_rgba(getattr(mat, "occlusionTexture", None))
        if getattr(mat, "roughnessFactor", None) is not None:
            out.roughness_factor = float(mat.roughnessFactor)
        if getattr(mat, "metallicFactor", None) is not None:
            out.metallic_factor = float(mat.metallicFactor)
        out.emissive_tex = _to_rgba(getattr(mat, "emissiveTexture", None))
        ef = getattr(mat, "emissiveFactor", None)
        if ef is not None:
            ef = np.asarray(ef, dtype=np.float64).ravel()
            out.emissive_factor = tuple(float(x) for x in ef[:3])
        # przezroczystosc (szklo): glTF trzyma opacity w alfie base color
        alpha_mode = str(getattr(mat, "alphaMode", "") or "").upper()
        if alpha_mode in ("BLEND", "MASK"):
            if out.base_color_tex is not None:
                a = out.base_color_tex.getchannel("A")
                out.opacity_tex = Image.merge("RGBA", (a, a, a, a))
            out.opacity_factor = float(out.base_color_factor[3])
    else:  # SimpleMaterial (OBJ/MTL)
        out.base_color_tex = _to_rgba(getattr(mat, "image", None))
        d = getattr(mat, "diffuse", None)
        if d is not None:
            d = np.asarray(d, dtype=np.float64).ravel()
            if d.max() > 1.0:
                d = d / 255.0
            if len(d) >= 3:
                out.base_color_factor = (d[0], d[1], d[2], 1.0)
        out.roughness_factor = 0.8
    return out
