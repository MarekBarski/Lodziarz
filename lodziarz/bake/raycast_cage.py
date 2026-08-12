"""STUB: bake przez raycast z cage'a (tryb eksperymentalny).

TODO:
  - zbudowac cage przez inflacje LOD0 wzdluz normali (parametr grubosci)
  - per texel nowego atlasu: raycast z cage'a w strone powierzchni zrodlowej
    (BVH po geometrii zrodlowej, np. embree albo wlasny BVH numpy)
  - sampling materialow zrodlowych w punkcie trafienia
  - obsluga miss: fallback do najblizszego trafienia sasiadow / dilation
Sens ma dopiero przy bake high->low poly (rozna topologia zrodla i celu);
dla samego re-atlasu texel-space jest szybszy i bezstratny.
"""
from __future__ import annotations

from . import BakeBackend, BakeOptions, BakeResult


class RaycastCageBackend(BakeBackend):
    name = "raycast"

    def bake(self, mesh, old_uvs, materials, opts, log) -> BakeResult:
        raise NotImplementedError(
            "backend 'raycast' to stub — uzyj 'texel' (patrz TODO w raycast_cage.py)")
