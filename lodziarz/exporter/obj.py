"""Export OBJ + MTL — format nie zna LOD-ow, wiec kazdy LOD to osobny plik
<Nazwa>_LOD0.obj... ze wspolnym <Nazwa>.mtl.

Material atlasowy dostaje mapy (map_Kd/norm/map_Pr/map_Pm/map_Ke wg
rozszerzenia PBR), materialy zrodlowe ida z sama nazwa — ich tekstury zyja
przy assecie zrodlowym (jak w FBX).
"""
from __future__ import annotations

import re
from pathlib import Path

from ..core import LodChain, MeshData
from ..logutil import PipelineLog


def _safe(name: str) -> str:
    """usemtl ze spacja/# wywraca czesc parserow."""
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name)


def _write_mtl(path: Path, chain: LodChain, texture_files: dict) -> None:
    baked_idx = chain.baked_material_index
    lines = ["# lodziarz"]
    for i, m in enumerate(chain.materials):
        lines.append(f"\nnewmtl {_safe(m.name)}")
        lines.append("Kd 0.8 0.8 0.8")
        if baked_idx is not None and i == baked_idx:
            if texture_files.get("basecolor"):
                lines.append(f"map_Kd {texture_files['basecolor']}")
            if texture_files.get("normal"):
                lines.append(f"norm {texture_files['normal']}")
            if texture_files.get("orm"):
                # PBR extension: roughness/metallic z kanalow ORM
                lines.append(f"map_Pr -imfchan g {texture_files['orm']}")
                lines.append(f"map_Pm -imfchan b {texture_files['orm']}")
            if texture_files.get("emissive"):
                lines.append(f"map_Ke {texture_files['emissive']}")
            if texture_files.get("opacity"):
                lines.append(f"map_d {texture_files['opacity']}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_obj(path: Path, name: str, mesh: MeshData, chain: LodChain,
               mtl_name: str) -> None:
    out = [f"# lodziarz\nmtllib {mtl_name}\no {name}"]
    pos, nrm, uv = mesh.positions, mesh.normals, mesh.uvs
    out.extend(f"v {p[0]:.6f} {p[1]:.6f} {p[2]:.6f}" for p in pos)
    out.extend(f"vt {t[0]:.6f} {t[1]:.6f}" for t in uv)
    out.extend(f"vn {n[0]:.6f} {n[1]:.6f} {n[2]:.6f}" for n in nrm)
    import numpy as np
    for mid in np.unique(mesh.tri_material):
        mid = int(mid)
        mat_name = chain.materials[mid].name \
            if 0 <= mid < len(chain.materials) else f"mat{mid}"
        out.append(f"usemtl {_safe(mat_name)}")
        for tri in mesh.indices[mesh.tri_material == mid] + 1:
            a, b, c = int(tri[0]), int(tri[1]), int(tri[2])
            out.append(f"f {a}/{a}/{a} {b}/{b}/{b} {c}/{c}/{c}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def export_obj(chain: LodChain, out_dir: Path, texture_files: dict,
               log: PipelineLog) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    mtl_name = f"{chain.asset_name}.mtl"
    _write_mtl(out_dir / mtl_name, chain, texture_files)
    paths = []
    for i, mesh in enumerate(chain.lods):
        name = f"{chain.asset_name}_LOD{i}"
        p = out_dir / f"{name}.obj"
        _write_obj(p, name, mesh, chain, mtl_name)
        paths.append(p)
    log.info(f"OBJ zapisany: {len(paths)} LOD-ow + {mtl_name}")
    return paths
