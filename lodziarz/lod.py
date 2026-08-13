"""Generacja lancucha LOD przez meshoptimizer simplifyWithAttributes.

LOD-y zachowuja UV i normale (atrybuty w simplifierze), wiec dziedzicza
atlas z LOD0 — bake robimy raz.
"""
from __future__ import annotations

import numpy as np

from .core import MeshData
from .logutil import PipelineLog
from .meshopt import simplify_with_attributes


def build_lod_chain(
    lod0: MeshData,
    count: int = 4,
    ratio: float = 0.5,
    normal_weight: float = 0.5,
    uv_weight: float = 1.0,
    log: PipelineLog | None = None,
    label_start: int = 0,
) -> list[MeshData]:
    """Zwraca [LOD0, LOD1, ...] — count pozycji, kazdy ~ratio^n trojkatow LOD0."""
    if count < 1:
        count = 1
    lods = [lod0]
    attributes = np.hstack([lod0.normals, lod0.uvs]).astype(np.float32)
    weights = np.array([normal_weight] * 3 + [uv_weight] * 2, dtype=np.float32)

    tri_of = _tri_material_lookup(lod0)
    prev_indices = lod0.indices
    for n in range(1, count):
        target = ratio ** n
        # budzet bledu rosnie z poziomem LOD — inaczej simplifier utyka
        # na gestych meshach zanim osiagnie docelowa liczbe trojkatow
        error_budget = min(0.5, 0.02 * (3.0 ** (n - 1)))
        new_indices, err = simplify_with_attributes(
            prev_indices, lod0.positions, attributes, weights,
            target_ratio=target / (len(prev_indices) / max(1, len(lod0.indices))),
            target_error=error_budget,
        )
        if len(new_indices) >= len(prev_indices) and log:
            log.warn(f"LOD{label_start + n}: simplifier nie zszedl nizej "
                     f"({len(new_indices)} tri) — mesh za prosty")
        tri_material = tri_of(new_indices)
        lods.append(MeshData(
            positions=lod0.positions,
            normals=lod0.normals,
            uvs=lod0.uvs,
            indices=new_indices,
            tri_material=tri_material,
            uvs2=lod0.uvs2,
        ))
        if log:
            log.info(f"LOD{label_start + n}: {len(new_indices)} tri "
                     f"(cel {target * 100:.0f}%, error {err:.4f})")
        prev_indices = new_indices
    return [_compact(m) for m in lods]


def _tri_material_lookup(lod0: MeshData):
    """Material dla uproszczonego trojkata: bierzemy material pierwszego
    wierzcholka wg mapy wierzcholek->material z LOD0 (wierzcholki sa wspolne)."""
    vert_mat = np.zeros(lod0.vertex_count, dtype=np.int32)
    vert_mat[lod0.indices.ravel()] = np.repeat(lod0.tri_material, 3)

    def lookup(indices: np.ndarray) -> np.ndarray:
        return vert_mat[indices[:, 0]]
    return lookup


def _compact(mesh: MeshData) -> MeshData:
    """Usuwa nieuzywane wierzcholki (po simplify zostaja osierocone)."""
    used, inverse = np.unique(mesh.indices.ravel(), return_inverse=True)
    remap = inverse.astype(np.uint32).reshape(-1, 3)
    return MeshData(
        positions=np.ascontiguousarray(mesh.positions[used]),
        normals=np.ascontiguousarray(mesh.normals[used]),
        uvs=np.ascontiguousarray(mesh.uvs[used]),
        indices=remap,
        tri_material=mesh.tri_material,
        uvs2=np.ascontiguousarray(mesh.uvs2[used]) if mesh.uvs2 is not None else None,
    )
