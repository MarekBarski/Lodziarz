"""Zapis map wyjsciowych: PNG (default) lub TGA, konwencja normal map GL/DX."""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from ..logutil import PipelineLog

SUFFIX = {
    "basecolor": "BaseColor",
    "normal": "Normal",
    "orm": "ORM",
    "ao": "AO",
    "roughness": "Roughness",
    "glossiness": "Glossiness",
    "metallic": "Metallic",
    "emissive": "Emissive",
    "opacity": "Opacity",
}


def flip_normal_g(img: Image.Image) -> Image.Image:
    """OpenGL <-> DirectX (inwersja kanalu G)."""
    arr = np.asarray(img.convert("RGB")).copy()
    arr[:, :, 1] = 255 - arr[:, :, 1]
    return Image.fromarray(arr, "RGB")


def save_textures(images: dict, asset_name: str, out_dir: Path,
                  fmt: str = "png", normal_directx: bool = False,
                  orm_split: bool = False, gloss: bool = False,
                  log: PipelineLog | None = None) -> dict:
    """Zwraca {klucz: nazwa pliku} (sciezki wzgledne do out_dir).

    orm_split: zamiast jednego ORM zapis AO + Roughness/Glossiness +
    Metallic osobno (gloss = inwersja roughness)."""
    fmt = fmt.lower()
    if fmt not in ("png", "tga"):
        raise ValueError(f"format tekstur '{fmt}' (png|tga)")
    out_dir.mkdir(parents=True, exist_ok=True)
    files = {}

    def save(key: str, img: Image.Image):
        fname = f"T_{asset_name}_{SUFFIX.get(key, key)}.{fmt}"
        img.save(out_dir / fname)
        files[key] = fname
        if log:
            log.info(f"tekstura: {fname} ({img.size[0]}x{img.size[1]})")

    for key, img in images.items():
        if img is None:
            continue
        if key == "normal" and normal_directx:
            img = flip_normal_g(img)   # OpenGL -> DirectX
        if key == "orm" and orm_split:
            arr = np.asarray(img.convert("RGB"))
            save("ao", Image.fromarray(arr[:, :, 0], "L"))
            if gloss:
                save("glossiness", Image.fromarray(255 - arr[:, :, 1], "L"))
            else:
                save("roughness", Image.fromarray(arr[:, :, 1], "L"))
            save("metallic", Image.fromarray(arr[:, :, 2], "L"))
            continue
        save(key, img)
    return files


def save_debug(result, asset_name: str, out_dir: Path,
               log: PipelineLog | None = None) -> None:
    dbg = out_dir / "debug"
    dbg.mkdir(parents=True, exist_ok=True)
    if result.uv_layout is not None:
        result.uv_layout.save(dbg / f"{asset_name}_uv_layout.png")
    if result.coverage is not None:
        result.coverage.save(dbg / f"{asset_name}_coverage.png")
    if log:
        log.info(f"debug: {dbg.name}/ (uv_layout, coverage)")
