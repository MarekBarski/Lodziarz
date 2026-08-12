"""Baker — wymienne strategie (BakeBackend).

Dostepne backendy:
  texel      — pelna implementacja: rasteryzacja nowego UV, sampling starych
               tekstur w texel space (moderngl, offscreen GPU)
  raycast    — STUB (TODO): bake przez raycast z cage'a, dla roznej geometrii
               zrodlo/cel (high->low poly)
  cameras26  — STUB (TODO): projekcja z 26 kamer wokol obiektu, debug/eksperymenty
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
             log: PipelineLog) -> BakeResult:
        """mesh.uvs = NOWY atlas UV; old_uvs = stare UV per wierzcholek."""


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
