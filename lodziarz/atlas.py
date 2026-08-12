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

    if len(new_indices) != len(mesh.indices):
        log.warn(f"xatlas zmienil liczbe trojkatow "
                 f"{len(mesh.indices)} -> {len(new_indices)}")

    vmapping = vmapping.astype(np.int64)
    old_uvs = np.ascontiguousarray(mesh.uvs[vmapping].astype(np.float32))

    # xatlas NIE gwarantuje kolejnosci trojkatow — material odtwarzamy
    # przez mape wierzcholek->material (majority z 3 rogow)
    vert_mat = np.zeros(mesh.vertex_count, dtype=np.int32)
    vert_mat[mesh.indices.ravel()] = np.repeat(mesh.tri_material, 3)
    corner_mats = vert_mat[vmapping[new_indices.astype(np.int64)]]  # (T,3)
    a, b, c = corner_mats[:, 0], corner_mats[:, 1], corner_mats[:, 2]
    tri_material = np.where((a == b) | (a == c), a, b).astype(np.int32)

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
