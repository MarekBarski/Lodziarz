"""STUB: bake przez projekcje z 26 kamer (tryb eksperymentalny/debug).

TODO:
  - 26 kierunkow = srodki scian/krawedzi/rogow szescianu wokol AABB obiektu
  - render obiektu ze starymi materialami z kazdej kamery (moderngl, depth)
  - per texel nowego atlasu: wybor najlepszej kamery (dot(normal, -dir),
    test widocznosci po depth), reprojekcja i sampling renderu
  - blend wag miedzy kamerami na szwach
Przydatne do debugowania artefaktow bake'u i jako fallback dla assetow
z uszkodzonymi UV zrodlowymi.
"""
from __future__ import annotations

from . import BakeBackend, BakeOptions, BakeResult


class Camera26Backend(BakeBackend):
    name = "cameras26"

    def bake(self, mesh, old_uvs, materials, opts, log) -> BakeResult:
        raise NotImplementedError(
            "backend 'cameras26' to stub — uzyj 'texel' (patrz TODO w camera26.py)")
