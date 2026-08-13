"""Cache wyniku procesu na dysku — re-export FBX/GLB z inna maska bake
per LOD bez ponownego przetwarzania.

Materialy serializujemy z teksturami jako PNG bytes (pickle golych PIL
trzymalby raw piksele — GB przy duzych assetach). Tozsamosc obrazow jest
zachowana (ten sam obiekt -> ten sam wpis), bo detekcja packed ORM opiera
sie na `roughness_tex is metallic_tex`.
"""
from __future__ import annotations

import dataclasses
import io
import pickle
from pathlib import Path

from PIL import Image

from .core import MaterialData, MeshData


def _pack_materials(mats: list[MaterialData]) -> dict:
    images: list[bytes] = []
    seen: dict[int, int] = {}

    def ref(img):
        key = id(img)
        if key not in seen:
            buf = io.BytesIO()
            img.save(buf, "PNG")
            seen[key] = len(images)
            images.append(buf.getvalue())
        return seen[key]

    packed = []
    for m in mats:
        d = {}
        for f in dataclasses.fields(m):
            v = getattr(m, f.name)
            d[f.name] = ("__img__", ref(v)) if isinstance(v, Image.Image) else v
        packed.append(d)
    return {"images": images, "materials": packed}


def _unpack_materials(data: dict) -> list[MaterialData]:
    images = [Image.open(io.BytesIO(b)) for b in data["images"]]
    out = []
    for d in data["materials"]:
        kw = {}
        for k, v in d.items():
            if isinstance(v, tuple) and len(v) == 2 and v[0] == "__img__":
                kw[k] = images[v[1]]
            else:
                kw[k] = v
        out.append(MaterialData(**kw))
    return out


def save_cache(path: Path, *, asset_name: str,
               baked_lods: list[MeshData], orig_lods: list[MeshData],
               materials: list[MaterialData], texture_files: dict,
               embed: bool, per_lod: bool,
               formats: dict | None = None) -> None:
    with open(path, "wb") as f:
        pickle.dump({
            "version": 1,
            "asset_name": asset_name,
            "baked_lods": baked_lods,
            "orig_lods": orig_lods,
            "materials": _pack_materials(materials),
            "texture_files": texture_files,
            "embed": embed,
            "per_lod": per_lod,
            "formats": formats or {"fbx": True, "glb": True, "obj": False},
        }, f)


def load_cache(path: Path) -> dict:
    with open(path, "rb") as f:
        data = pickle.load(f)
    data["materials"] = _unpack_materials(data["materials"])
    return data
