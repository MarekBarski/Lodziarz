"""Regresja _carry_tri_material — granice materialow per trojkat 1:1,
bez glosowania (krytyczny bug z LOG_2026-08-13: zygzak materialow)."""
import numpy as np

from lodziarz.atlas import _carry_tri_material
from lodziarz.core import MeshData


def _quad_mesh() -> MeshData:
    # 2 trojkaty, 2 rozne materialy
    pos = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]],
                   dtype=np.float32)
    idx = np.array([[0, 1, 2], [0, 2, 3]], dtype=np.uint32)
    return MeshData(positions=pos, normals=np.tile([0, 0, 1], (4, 1)).astype(np.float32),
                    uvs=np.zeros((4, 2), dtype=np.float32), indices=idx,
                    tri_material=np.array([0, 1], dtype=np.int32))


def test_fast_path_identity(log):
    mesh = _quad_mesh()
    vmapping = np.arange(4, dtype=np.int64)
    out = _carry_tri_material(mesh, vmapping, mesh.indices.astype(np.int64), log)
    assert out.tolist() == [0, 1]


def test_fast_path_with_duplicated_seam_vertex(log):
    # xatlas zduplikowal wierzcholek 2 -> nowy index 4; kolejnosc trojkatow
    # zachowana, pozycje sie zgadzaja => kopiowanie 1:1
    mesh = _quad_mesh()
    vmapping = np.array([0, 1, 2, 3, 2], dtype=np.int64)
    new_indices = np.array([[0, 1, 4], [0, 2, 3]], dtype=np.int64)
    out = _carry_tri_material(mesh, vmapping, new_indices, log)
    assert out.tolist() == [0, 1]


def test_fallback_reordered_triangles(log):
    # xatlas zmienil kolejnosc trojkatow -> mapowanie po tuplach wierzcholkow
    mesh = _quad_mesh()
    vmapping = np.arange(4, dtype=np.int64)
    new_indices = np.array([[0, 2, 3], [0, 1, 2]], dtype=np.int64)  # swap
    out = _carry_tri_material(mesh, vmapping, new_indices, log)
    assert out.tolist() == [1, 0]
