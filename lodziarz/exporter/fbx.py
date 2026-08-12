"""Export FBX przez fbx_writer.exe (Autodesk FBX SDK — podejscie z Brutgena).

Python zapisuje format posredni LZMESH, natywny writer sklada scene FBX
z FbxLODGroup (naming zgodny z Unreal: <Nazwa>_LOD0, _LOD1...).
"""
from __future__ import annotations

import struct
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from ..core import LodChain, MeshData, bin_dir
from ..logutil import PipelineLog


def _pack_str(s: str) -> bytes:
    b = s.encode("utf-8")
    return struct.pack("<I", len(b)) + b


def _write_lzmesh(path: Path, name: str, lods: list[MeshData],
                  materials: list[dict], thresholds: list[float],
                  y_up: bool = True, scale: float = 100.0) -> None:
    with open(path, "wb") as f:
        f.write(b"LZMESH1\0")
        f.write(_pack_str(name))
        f.write(struct.pack("<BBHf", 1 if y_up else 0, 0, 0, scale))
        f.write(struct.pack("<I", len(materials)))
        for m in materials:
            f.write(_pack_str(m["name"]))
            for key in ("basecolor", "normal", "orm", "emissive"):
                f.write(_pack_str(m.get(key, "")))
        f.write(struct.pack("<II", len(lods), len(thresholds)))
        if thresholds:
            f.write(np.asarray(thresholds, dtype=np.float32).tobytes())
        for mesh in lods:
            f.write(struct.pack("<II", mesh.vertex_count, mesh.triangle_count))
            f.write(np.ascontiguousarray(mesh.positions, dtype=np.float32).tobytes())
            f.write(np.ascontiguousarray(mesh.normals, dtype=np.float32).tobytes())
            f.write(np.ascontiguousarray(mesh.uvs, dtype=np.float32).tobytes())
            f.write(np.ascontiguousarray(mesh.indices, dtype=np.uint32).tobytes())
            f.write(np.ascontiguousarray(mesh.tri_material, dtype=np.int32).tobytes())


def _run_writer(lzmesh: Path, out_fbx: Path, log: PipelineLog,
                embed: bool = False) -> None:
    exe = bin_dir() / "fbx_writer.exe"
    if not exe.exists():
        raise FileNotFoundError(f"brak {exe} — zbuduj native/build_native.ps1")
    cmd = [str(exe), str(lzmesh), str(out_fbx)]
    if embed:
        cmd.append("--embed")
    proc = subprocess.run(cmd,
                          capture_output=True, text=True, timeout=600,
                          creationflags=subprocess.CREATE_NO_WINDOW)
    if proc.returncode != 0:
        raise RuntimeError(f"fbx_writer: {proc.stderr.strip() or 'nieznany blad'}")
    log.info(f"FBX zapisany: {out_fbx.name}")


def _auto_thresholds(lod0: MeshData, count: int) -> list[float]:
    """Progi LODGroup w cm — skalowane rozmiarem obiektu."""
    if count < 2:
        return []
    ext = lod0.positions.max(axis=0) - lod0.positions.min(axis=0)
    diag_cm = float(np.linalg.norm(ext)) * 100.0
    base = max(diag_cm, 100.0)
    return [base * 5.0 * (2.0 ** i) for i in range(count - 1)]


def _material_entries(chain: LodChain, texture_files: dict) -> list[dict]:
    """Tekstury atlasu podpinamy tylko pod material baked; oryginalne
    materialy ida z sama nazwa (ich tekstury zyja przy assecie zrodlowym)."""
    baked_idx = chain.baked_material_index
    out = []
    for i, m in enumerate(chain.materials):
        is_baked = baked_idx is not None and i == baked_idx
        out.append({
            "name": m.name,
            "basecolor": texture_files.get("basecolor", "") if is_baked else "",
            "normal": texture_files.get("normal", "") if is_baked else "",
            "orm": texture_files.get("orm", "") if is_baked else "",
            "emissive": texture_files.get("emissive", "") if is_baked else "",
        })
    return out


def _absolutize(mats: list[dict], out_dir: Path) -> list[dict]:
    """Przy embedowaniu SDK musi znalezc pliki tekstur — pelne sciezki."""
    out = []
    for m in mats:
        m = dict(m)
        for key in ("basecolor", "normal", "orm", "emissive"):
            if m.get(key):
                m[key] = str((out_dir / m[key]).resolve())
        out.append(m)
    return out


def export_fbx_lodgroup(chain: LodChain, out_path: Path,
                        texture_files: dict, log: PipelineLog,
                        embed: bool = False) -> Path:
    """One-pass: node LODGroup <Nazwa>, dzieci <Nazwa>_LOD0..N — UE importuje
    calosc jednym plikiem z Import Mesh LODs."""
    mats = _material_entries(chain, texture_files)
    if embed:
        mats = _absolutize(mats, out_path.parent)
    thresholds = _auto_thresholds(chain.lods[0], len(chain.lods))
    with tempfile.TemporaryDirectory(prefix="lodziarz_") as td:
        lz = Path(td) / "asset.lzmesh"
        _write_lzmesh(lz, chain.asset_name, chain.lods, mats, thresholds)
        _run_writer(lz, out_path, log, embed=embed)
    return out_path


def export_fbx_per_lod(chain: LodChain, out_dir: Path,
                       texture_files: dict, log: PipelineLog,
                       embed: bool = False) -> list[Path]:
    """Tryb alternatywny: SM_<Nazwa>_LOD0.fbx, SM_<Nazwa>_LOD1.fbx..."""
    mats = _material_entries(chain, texture_files)
    if embed:
        mats = _absolutize(mats, out_dir)
    base = chain.asset_name
    if not base.startswith("SM_"):
        base = f"SM_{base}"
    paths = []
    with tempfile.TemporaryDirectory(prefix="lodziarz_") as td:
        for i, mesh in enumerate(chain.lods):
            name = f"{base}_LOD{i}"
            lz = Path(td) / f"{name}.lzmesh"
            _write_lzmesh(lz, name, [mesh], mats, [])
            out = out_dir / f"{name}.fbx"
            _run_writer(lz, out, log, embed=embed)
            paths.append(out)
    return paths
