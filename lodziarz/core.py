"""Wspolny model danych: mesh + materialy, niezalezny od formatu wejsciowego."""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image


def resource_dir() -> Path:
    """Katalog zasobow — dziala i z repo, i z PyInstaller onefile."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "lodziarz"
    return Path(__file__).parent


def bin_dir() -> Path:
    return resource_dir() / "bin"


@dataclass
class MaterialData:
    name: str = "Material"
    base_color_tex: Image.Image | None = None
    base_color_factor: tuple = (0.8, 0.8, 0.8, 1.0)
    normal_tex: Image.Image | None = None
    # ORM skladowe — moga wskazywac te sama teksture (packed glTF)
    occlusion_tex: Image.Image | None = None   # kanal R
    roughness_tex: Image.Image | None = None   # kanal G (glTF MR)
    metallic_tex: Image.Image | None = None    # kanal B (glTF MR)
    roughness_factor: float = 1.0
    metallic_factor: float = 0.0
    emissive_tex: Image.Image | None = None
    emissive_factor: tuple = (0.0, 0.0, 0.0)
    opacity_tex: Image.Image | None = None     # szklo itp.; None = nieprzezroczysty
    opacity_factor: float = 1.0

    def has_emissive(self) -> bool:
        return self.emissive_tex is not None or any(c > 0.0 for c in self.emissive_factor)

    def has_opacity(self) -> bool:
        return self.opacity_tex is not None or self.opacity_factor < 1.0


@dataclass
class MeshData:
    """Trojkatny mesh indeksowany, atrybuty per wierzcholek."""
    positions: np.ndarray                      # (V,3) f32, metry
    normals: np.ndarray                        # (V,3) f32
    uvs: np.ndarray                            # (V,2) f32
    indices: np.ndarray                        # (T,3) u32
    tri_material: np.ndarray                   # (T,)  i32
    uvs2: np.ndarray | None = None             # (V,2) f32 — stare UV po bake (podglad)

    @property
    def vertex_count(self) -> int:
        return len(self.positions)

    @property
    def triangle_count(self) -> int:
        return len(self.indices)

    def validate(self) -> None:
        v = self.vertex_count
        assert self.normals.shape == (v, 3), "normals shape"
        assert self.uvs.shape == (v, 2), "uvs shape"
        assert self.indices.ndim == 2 and self.indices.shape[1] == 3, "indices shape"
        assert len(self.tri_material) == len(self.indices), "tri_material len"
        if v:
            assert int(self.indices.max()) < v, "index poza zakresem wierzcholkow"


@dataclass
class Asset:
    name: str
    mesh: MeshData
    materials: list[MaterialData] = field(default_factory=list)
    source_path: str = ""


@dataclass
class LodChain:
    """LOD0..N — wszystkie dziela ten sam zestaw materialow (i atlas po bake)."""
    asset_name: str
    lods: list[MeshData]
    materials: list[MaterialData]


def weld_vertices(positions, normals, uvs, tri_material):
    """Sklejenie identycznych wierzcholkow (pos+nrm+uv) w mesh indeksowany.

    Wejscie: atrybuty rozwiniete per-corner (3 na trojkat).
    """
    corners = np.hstack([
        positions.astype(np.float32),
        normals.astype(np.float32),
        uvs.astype(np.float32),
    ])
    uniq, inverse = np.unique(corners, axis=0, return_inverse=True)
    indices = inverse.astype(np.uint32).reshape(-1, 3)
    return MeshData(
        positions=np.ascontiguousarray(uniq[:, 0:3]),
        normals=np.ascontiguousarray(uniq[:, 3:6]),
        uvs=np.ascontiguousarray(uniq[:, 6:8]),
        indices=np.ascontiguousarray(indices),
        tri_material=np.asarray(tri_material, dtype=np.int32),
    )


def compute_smooth_normals(positions: np.ndarray, indices: np.ndarray) -> np.ndarray:
    """Normale z geometrii (area-weighted), gdy plik ich nie ma."""
    n = np.zeros_like(positions, dtype=np.float64)
    tri = positions[indices]
    face_n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    for c in range(3):
        np.add.at(n, indices[:, c], face_n)
    length = np.linalg.norm(n, axis=1, keepdims=True)
    length[length < 1e-20] = 1.0
    return (n / length).astype(np.float32)


def compute_tangents(mesh: MeshData, uvs: np.ndarray) -> np.ndarray:
    """Tangenty per wierzcholek dla danego zestawu UV. Zwraca (V,4) f32 (w = handedness)."""
    v = mesh.vertex_count
    tan = np.zeros((v, 3), dtype=np.float64)
    bitan = np.zeros((v, 3), dtype=np.float64)
    idx = mesh.indices
    p = mesh.positions[idx]     # (T,3,3)
    t = uvs[idx]                # (T,3,2)
    e1 = p[:, 1] - p[:, 0]
    e2 = p[:, 2] - p[:, 0]
    du1 = t[:, 1, 0] - t[:, 0, 0]
    dv1 = t[:, 1, 1] - t[:, 0, 1]
    du2 = t[:, 2, 0] - t[:, 0, 0]
    dv2 = t[:, 2, 1] - t[:, 0, 1]
    det = du1 * dv2 - du2 * dv1
    det[np.abs(det) < 1e-20] = 1e-20
    r = (1.0 / det)[:, None]
    ft = (e1 * dv2[:, None] - e2 * dv1[:, None]) * r
    fb = (e2 * du1[:, None] - e1 * du2[:, None]) * r
    for c in range(3):
        np.add.at(tan, idx[:, c], ft)
        np.add.at(bitan, idx[:, c], fb)
    n = mesh.normals.astype(np.float64)
    # Gram-Schmidt wzgledem normali
    tan -= n * np.sum(tan * n, axis=1, keepdims=True)
    ln = np.linalg.norm(tan, axis=1, keepdims=True)
    degenerate = (ln < 1e-12)[:, 0]
    ln[ln < 1e-12] = 1.0
    tan /= ln
    # fallback dla zdegenerowanych: dowolny wektor prostopadly do normali
    if degenerate.any():
        alt = np.cross(n[degenerate], np.array([0.0, 1.0, 0.0]))
        bad = np.linalg.norm(alt, axis=1) < 1e-6
        alt[bad] = np.cross(n[degenerate][bad], np.array([1.0, 0.0, 0.0]))
        alt /= np.linalg.norm(alt, axis=1, keepdims=True)
        tan[degenerate] = alt
    w = np.where(np.sum(np.cross(n, tan) * bitan, axis=1) < 0.0, -1.0, 1.0)
    return np.hstack([tan, w[:, None]]).astype(np.float32)
