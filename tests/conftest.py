"""Wspolne fabryki geometrii testowej — syntetyczne, zero binarek w repo."""
from __future__ import annotations

import numpy as np
import pytest

from lodziarz.core import MeshData
from lodziarz.logutil import PipelineLog


def make_uv_sphere(n_lat: int = 24, n_lon: int = 48) -> MeshData:
    """Gladka kula (wspolne wierzcholki, smooth normale) — ~2300 tri."""
    lats = np.linspace(0.0, np.pi, n_lat + 1)
    lons = np.linspace(0.0, 2.0 * np.pi, n_lon + 1)
    lat, lon = np.meshgrid(lats, lons, indexing="ij")
    x = np.sin(lat) * np.cos(lon)
    y = np.cos(lat)
    z = np.sin(lat) * np.sin(lon)
    pos = np.stack([x, y, z], axis=-1).reshape(-1, 3).astype(np.float32)
    uv = np.stack([lon / (2 * np.pi), 1.0 - lat / np.pi],
                  axis=-1).reshape(-1, 2).astype(np.float32)
    cols = n_lon + 1
    tris = []
    for i in range(n_lat):
        for j in range(n_lon):
            a = i * cols + j
            b = a + 1
            c = a + cols
            d = c + 1
            if i > 0:
                tris.append((a, c, b))
            if i < n_lat - 1:
                tris.append((b, c, d))
    indices = np.asarray(tris, dtype=np.uint32)
    normals = pos / np.linalg.norm(pos, axis=1, keepdims=True)
    return MeshData(positions=pos, normals=normals.astype(np.float32), uvs=uv,
                    indices=indices,
                    tri_material=np.zeros(len(indices), dtype=np.int32))


def make_flat_shaded(mesh: MeshData) -> MeshData:
    """Wersja faceted: kazdy trojkat ma wlasne wierzcholki i flat normale —
    kazda krawedz jest hard edge (symulacja hard-surface z 3ds Max)."""
    idx = mesh.indices.astype(np.int64).ravel()
    pos = mesh.positions[idx]
    uv = mesh.uvs[idx]
    tri = pos.reshape(-1, 3, 3)
    fn = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    ln = np.linalg.norm(fn, axis=1, keepdims=True)
    ln[ln < 1e-12] = 1.0
    nrm = np.repeat(fn / ln, 3, axis=0).astype(np.float32)
    n = len(pos)
    return MeshData(positions=pos.astype(np.float32), normals=nrm,
                    uvs=uv.astype(np.float32),
                    indices=np.arange(n, dtype=np.uint32).reshape(-1, 3),
                    tri_material=mesh.tri_material.copy())


@pytest.fixture
def log() -> PipelineLog:
    return PipelineLog(echo=False)
