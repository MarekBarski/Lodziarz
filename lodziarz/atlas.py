"""Nowy UV: flat unwrap + packing wysp przez xatlas."""
from __future__ import annotations

import numpy as np
import xatlas

from .core import MeshData
from .logutil import PipelineLog


def unwrap_atlas(
    mesh: MeshData,
    resolution: int,
    padding: int,
    log: PipelineLog,
) -> tuple[MeshData, np.ndarray]:
    """Zwraca (mesh z nowym UV w .uvs, old_uvs (V',2) — stare UV per nowy wierzcholek).

    xatlas moze duplikowac wierzcholki na szwach — vmapping mapuje nowe -> stare.
    """
    log.info(f"xatlas: unwrap + pack (res {resolution}, padding {padding}px)")
    atlas = xatlas.Atlas()
    atlas.add_mesh(mesh.positions.astype(np.float32),
                   mesh.indices.astype(np.uint32),
                   mesh.normals.astype(np.float32))
    chart = xatlas.ChartOptions()
    pack = xatlas.PackOptions()
    pack.resolution = int(resolution)
    pack.padding = int(padding)
    pack.bilinear = True
    pack.blockAlign = True
    atlas.generate(chart_options=chart, pack_options=pack)
    vmapping, new_indices, new_uvs = atlas.get_mesh(0)

    vmapping = vmapping.astype(np.int64)
    old_uvs = np.ascontiguousarray(mesh.uvs[vmapping].astype(np.float32))

    # material per trojkat przenosimy 1:1 — xatlas zachowuje kolejnosc
    # trojkatow (weryfikacja po pozycjach rogow); zadnej rekonstrukcji
    tri_material = _carry_tri_material(mesh, vmapping,
                                       new_indices.astype(np.int64), log)

    out = MeshData(
        positions=np.ascontiguousarray(mesh.positions[vmapping]),
        normals=np.ascontiguousarray(mesh.normals[vmapping]),
        uvs=np.ascontiguousarray(new_uvs.astype(np.float32)),
        indices=np.ascontiguousarray(new_indices.astype(np.uint32)),
        tri_material=tri_material,
        uvs2=old_uvs,   # stare UV — podglad w viewerze
    )
    log.info(f"atlas: {out.vertex_count} verts po unwrap "
             f"(bylo {mesh.vertex_count})")
    return out, old_uvs


def _carry_tri_material(mesh: MeshData, vmapping: np.ndarray,
                        new_indices: np.ndarray, log: PipelineLog) -> np.ndarray:
    """Material per trojkat po unwrap — bez glosowania, mapowanie exact."""
    orig_idx = mesh.indices.astype(np.int64)
    if len(new_indices) == len(orig_idx):
        same = np.allclose(mesh.positions[orig_idx],
                           mesh.positions[vmapping[new_indices]])
        if same:
            return mesh.tri_material.copy()

    # fallback: xatlas zmienil kolejnosc/liczbe — mapuj po tuplach
    # oryginalnych wierzcholkow
    log.warn("xatlas zmienil kolejnosc/liczbe trojkatow — mapowanie po tuplach")
    tri_of = {tuple(t): i for i, t in enumerate(np.sort(orig_idx, axis=1))}
    out = np.zeros(len(new_indices), dtype=np.int32)
    missing = 0
    for i, t in enumerate(np.sort(vmapping[new_indices], axis=1)):
        j = tri_of.get(tuple(t))
        if j is None:
            missing += 1
        else:
            out[i] = mesh.tri_material[j]
    if missing:
        log.warn(f"{missing} trojkatow bez odpowiednika po unwrap "
                 f"(material 0)")
    return out
