"""Raport walidacji assetu po imporcie — checklista przed bake.

Loguje ostrzezenia (GUI + CLI) i zwraca dict, ktory laduje w manifescie
i w raporcie zbiorczym batcha.
"""
from __future__ import annotations

import numpy as np

from .core import Asset
from .logutil import PipelineLog

# sloty sprawdzane per material (nazwa -> atrybut MaterialData)
_MAP_SLOTS = {
    "basecolor": "base_color_tex",
    "normal": "normal_tex",
    "roughness": "roughness_tex",
    "metallic": "metallic_tex",
    "ao": "occlusion_tex",
}


def validation_report(asset: Asset, log: PipelineLog) -> dict:
    mesh = asset.mesh
    report: dict = {
        "tris": int(mesh.triangle_count),
        "verts": int(mesh.vertex_count),
        "materials": len(asset.materials),
        "uv_missing": False,
        "degenerate_tris": 0,
        "missing_maps": {},
        "warnings": [],
    }

    def warn(msg: str) -> None:
        report["warnings"].append(msg)
        log.warn(f"walidacja: {msg}")

    # UV: all-zero = brak wspolrzednych w pliku (unwrap i tak zrobimy,
    # ale texel-space bake nie ma zrodla do samplowania)
    if mesh.uvs.size and not np.any(mesh.uvs):
        report["uv_missing"] = True
        warn("brak UV w zrodle — bake bedzie samplowal plaskie kolory materialow")

    # trojkaty zdegenerowane (zerowe pole) — psuja unwrap i simplify
    tri = mesh.positions[mesh.indices.astype(np.int64)]
    area2 = np.linalg.norm(
        np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1)
    degenerate = int((area2 < 1e-14).sum())
    report["degenerate_tris"] = degenerate
    if degenerate:
        warn(f"{degenerate} zdegenerowanych trojkatow (zerowe pole)")

    # materialy nieuzywane przez zaden trojkat
    used = set(np.unique(mesh.tri_material).tolist())
    unused = [m.name for i, m in enumerate(asset.materials) if i not in used]
    if unused:
        warn(f"materialy bez trojkatow: {', '.join(unused[:8])}"
             + (f" +{len(unused) - 8}" if len(unused) > 8 else ""))

    # brakujace mapy per material (emissive/opacity to cechy, nie braki)
    for m in asset.materials:
        missing = [slot for slot, attr in _MAP_SLOTS.items()
                   if getattr(m, attr) is None]
        if missing:
            report["missing_maps"][m.name] = missing
    no_basecolor = [name for name, miss in report["missing_maps"].items()
                    if "basecolor" in miss and name != "__default__"]
    if no_basecolor:
        warn(f"bez basecolor (bake da plaski kolor): "
             f"{', '.join(no_basecolor[:8])}"
             + (f" +{len(no_basecolor) - 8}" if len(no_basecolor) > 8 else ""))

    log.info(f"walidacja: {report['tris']} tri, {report['verts']} verts, "
             f"{report['materials']} materialow, "
             f"{len(report['warnings'])} ostrzezen")
    return report
