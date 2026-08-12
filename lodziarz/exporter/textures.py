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
    "emissive": "Emissive",
    "opacity": "Opacity",
}


def save_textures(images: dict, asset_name: str, out_dir: Path,
                  fmt: str = "png", normal_directx: bool = False,
                  log: PipelineLog | None = None) -> dict:
    """Zwraca {klucz: nazwa pliku} (sciezki wzgledne do out_dir)."""
    fmt = fmt.lower()
    if fmt not in ("png", "tga"):
        raise ValueError(f"format tekstur '{fmt}' (png|tga)")
    out_dir.mkdir(parents=True, exist_ok=True)
    files = {}
    for key, img in images.items():
        if img is None:
            continue
        if key == "normal" and normal_directx:
            arr = np.asarray(img.convert("RGB")).copy()
            arr[:, :, 1] = 255 - arr[:, :, 1]  # flip G: OpenGL -> DirectX
            img = Image.fromarray(arr, "RGB")
        fname = f"T_{asset_name}_{SUFFIX.get(key, key)}.{fmt}"
        img.save(out_dir / fname)
        files[key] = fname
        if log:
            log.info(f"tekstura: {fname} ({img.size[0]}x{img.size[1]})")
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
