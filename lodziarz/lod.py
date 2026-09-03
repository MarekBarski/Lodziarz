"""Generacja lancucha LOD przez meshoptimizer simplifyWithAttributes.

LOD-y zachowuja UV i normale (atrybuty w simplifierze), wiec dziedzicza
atlas z LOD0 — bake robimy raz.
"""
from __future__ import annotations

import numpy as np

from .core import MeshData
from .logutil import PipelineLog
from .meshopt import simplify_with_attributes


def smooth_weld_mesh(mesh: MeshData, log: PipelineLog | None = None) -> MeshData:
    """Skleja wierzcholki rozdzielone TYLKO normalna (hard edges / flat shading).

    Klucz sklejania to pozycja + UV, wiec szwy UV (atlas!) zostaja nietkniete;
    znikaja wylacznie rozciecia od twardych krawedzi, ktore simplifier
    traktuje jak granice i przez ktore "nie schodzi" na hard-surface.
    Normale w grupie sa usredniane — cieniowanie krawedzi mieknie (swiadomy
    trade-off, tryb opcjonalny)."""
    key = np.hstack([mesh.positions, mesh.uvs]).astype(np.float32)
    uniq, first_idx, inverse = np.unique(key, axis=0, return_index=True,
                                         return_inverse=True)
    if len(uniq) == len(mesh.positions):
        if log:
            log.info("smooth-weld: brak rozdwojonych wierzcholkow — nic do sklejenia")
        return mesh
    normals = np.zeros((len(uniq), 3), dtype=np.float64)
    np.add.at(normals, inverse, mesh.normals.astype(np.float64))
    ln = np.linalg.norm(normals, axis=1, keepdims=True)
    ln[ln < 1e-12] = 1.0
    normals = (normals / ln).astype(np.float32)
    if log:
        log.info(f"smooth-weld: {mesh.vertex_count} -> {len(uniq)} verts "
                 f"(sklejone hard edges przed simplify)")
    return MeshData(
        positions=np.ascontiguousarray(uniq[:, 0:3]),
        normals=normals,
        uvs=np.ascontiguousarray(uniq[:, 3:5]),
        indices=inverse.astype(np.uint32)[mesh.indices.ravel()].reshape(-1, 3),
        tri_material=mesh.tri_material,
        uvs2=np.ascontiguousarray(mesh.uvs2[first_idx])
            if mesh.uvs2 is not None else None,
    )


def build_lod_chain(
    lod0: MeshData,
    count: int = 4,
    ratio: float = 0.5,
    normal_weight: float = 0.5,
    uv_weight: float = 1.0,
    log: PipelineLog | None = None,
    label_start: int = 0,
    smooth_weld: bool = False,
) -> list[MeshData]:
    """Zwraca [LOD0, LOD1, ...] — count pozycji, kazdy ~ratio^n trojkatow LOD0.

    smooth_weld: LOD0 zostaje oryginalny, ale LOD1+ sa upraszczane na siatce
    ze sklejonymi hard edges (wiecej redukcji kosztem miekszego cieniowania)."""
    if count < 1:
        count = 1
    lods = [lod0]
    src = smooth_weld_mesh(lod0, log) if smooth_weld else lod0
    attributes = np.hstack([src.normals, src.uvs]).astype(np.float32)
    weights = np.array([normal_weight] * 3 + [uv_weight] * 2, dtype=np.float32)

    tri_of = _tri_material_lookup(src)
    prev_indices = src.indices
    for n in range(1, count):
        target = ratio ** n
        # budzet bledu rosnie z poziomem LOD — inaczej simplifier utyka
        # na gestych meshach zanim osiagnie docelowa liczbe trojkatow
        error_budget = min(0.5, 0.02 * (3.0 ** (n - 1)))
        new_indices, err = simplify_with_attributes(
            prev_indices, src.positions, attributes, weights,
            target_ratio=target / (len(prev_indices) / max(1, len(src.indices))),
            target_error=error_budget,
        )
        if len(new_indices) >= len(prev_indices) and log:
            hint = ("" if smooth_weld else
                    " — sprobuj trybu smooth-weld (hard edges blokuja simplify)")
            log.warn(f"LOD{label_start + n}: simplifier nie zszedl nizej "
                     f"({len(new_indices)} tri){hint}")
        tri_material = tri_of(new_indices)
        lods.append(MeshData(
            positions=src.positions,
            normals=src.normals,
            uvs=src.uvs,
            indices=new_indices,
            tri_material=tri_material,
            uvs2=src.uvs2,
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
