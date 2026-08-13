"""Baker — wymienne strategie (BakeBackend).

Dostepne backendy:
  texel      — default: rasteryzacja nowego UV, sampling starych tekstur
               w texel space (moderngl, offscreen GPU); najszybszy
  raycast    — raycast z cage'a (inflacja wzdluz normali + BVH); CPU,
               wolniejszy; przydatny przy nachodzacych shellach
  cameras26  — projekcja z 26 kamer ortho wokol obiektu z testem widocznosci
               po depth; do debugowania artefaktow bake'u
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import numpy as np
from PIL import Image

from ..core import MaterialData, MeshData
from ..logutil import PipelineLog


@dataclass
class BakeOptions:
    resolution: int = 2048          # 512..4096
    dilation: int = 8               # px marginesu wysp
    ssaa: int = 2                   # supersampling rasteryzacji
    input_normal_flip_g: bool = False  # True gdy wejsciowe normalki sa DirectX
    cage_offset: float = 0.0        # raycast: inflacja cage'a w m (0 = auto 1% diag)


@dataclass
class BakeResult:
    # klucze: "basecolor", "normal", "orm", opcjonalnie "emissive"
    images: dict = field(default_factory=dict)
    coverage: Image.Image | None = None     # debug: mapa pokrycia texeli
    uv_layout: Image.Image | None = None    # debug: PNG z UV layoutem


class BakeBackend(ABC):
    name = "base"

    @abstractmethod
    def bake(self, mesh: MeshData, old_uvs: np.ndarray,
             materials: list[MaterialData], opts: BakeOptions,
             log: PipelineLog, source: MeshData | None = None) -> BakeResult:
        """mesh (cel): .uvs = NOWY atlas UV; old_uvs = stare UV per wierzcholek
        celu (uzywa texel). source (projekcja): oryginalna siatka zrodlowa —
        stare UV w .uvs, materialy w .tri_material; material i UV per texel
        wynikaja z punktu trafienia na source (per pixel, nie per trojkat)."""


def get_backend(name: str) -> BakeBackend:
    from .texel_space import TexelSpaceBackend
    from .raycast_cage import RaycastCageBackend
    from .camera26 import Camera26Backend
    backends = {
        "texel": TexelSpaceBackend,
        "raycast": RaycastCageBackend,
        "cameras26": Camera26Backend,
    }
    if name not in backends:
        raise ValueError(f"nieznany backend bake '{name}' "
                         f"(dostepne: {', '.join(backends)})")
    return backends[name]()
