"""Zewnetrzne przypisanie map do materialow.

FBX z UE/DCC czesto nie zawiera tekstur (same nazwy materialow). Uzupelniamy
mapy z dwoch zrodel, w tej kolejnosci:

1. sidecar JSON `<plik>.textures.json` obok pliku wejsciowego:
   {
     "MI_SteelMid": {
       "basecolor": "T_Steel_BC.png", "normal": "T_Steel_N.png",
       "orm": "T_Steel_ORM.png",
       // albo osobno: "occlusion" / "roughness" / "metallic" / "gloss"
       "emissive": "", "opacity": ""
     },
     "*": { ... }   // fallback dla wszystkich niewymienionych
   }
   Sciezki wzgledne = wzgledem folderu pliku wejsciowego.

2. konwencja nazw — szukamy w folderze pliku i podfolderze textures/:
   <Material>_BaseColor|_Albedo|_BC|_D  |  _Normal|_N  |  _ORM
   _AO|_Occlusion  |  _Roughness|_R  |  _Metallic|_M
   _Gloss (odwracany do roughness)  |  _Emissive|_E  |  _Opacity|_A

Mapy juz obecne w materiale (np. embedded FBX) nie sa nadpisywane
przez konwencje nazw; sidecar JSON nadpisuje zawsze.
"""
from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageOps

from .core import Asset, MaterialData
from .logutil import PipelineLog

SUFFIXES = {
    "basecolor": ["basecolor", "albedo", "bc", "d", "diffuse", "base_color"],
    "normal": ["normal", "n", "nrm"],
    "orm": ["orm", "occlusionroughnessmetallic", "arm"],
    "occlusion": ["ao", "occlusion"],
    "roughness": ["roughness", "r", "rough"],
    "metallic": ["metallic", "m", "metal"],
    "gloss": ["gloss", "glossiness"],
    "emissive": ["emissive", "e", "emission"],
    "opacity": ["opacity", "a", "alpha"],
}
IMG_EXTS = [".png", ".jpg", ".jpeg", ".tga", ".bmp", ".tiff", ".webp"]


def apply_texture_overrides(asset: Asset, source_path: Path,
                            log: PipelineLog) -> None:
    base_dir = Path(source_path).parent
    sidecar = _load_sidecar(source_path, log)
    index = _index_images(base_dir)

    for mat in asset.materials:
        entry = sidecar.get(mat.name) or sidecar.get("*")
        if entry:
            _apply_sidecar_entry(mat, entry, base_dir, log)
        _apply_name_convention(mat, index, asset.name, log)


def apply_manual_assignments(asset: Asset, mapping: dict,
                             log: PipelineLog) -> None:
    """Mapy przypisane recznie w GUI: {material: {slot: sciezka}} — nadpisuja
    wszystko (forced)."""
    if not mapping:
        return
    by_name = {m.name: m for m in asset.materials}
    for mat_name, slots in mapping.items():
        mat = by_name.get(mat_name)
        if mat is None:
            log.warn(f"przypisanie map: nieznany material '{mat_name}'")
            continue
        for key, raw in (slots or {}).items():
            if key not in SUFFIXES or not raw:
                continue
            p = Path(raw)
            if not p.exists():
                log.warn(f"  {mat_name}: {key} nie istnieje: {raw}")
                continue
            img = _open(p, log)
            if img is not None:
                _assign(mat, key, img, p.name, log, forced=True)


def _load_sidecar(source_path: Path, log: PipelineLog) -> dict:
    p = Path(str(source_path) + ".textures.json")
    if not p.exists():
        p = source_path.with_suffix(".textures.json")
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        log.info(f"sidecar tekstur: {p.name} ({len(data)} wpisow)")
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError) as e:
        log.warn(f"sidecar {p.name}: {e}")
        return {}


def _index_images(base_dir: Path) -> dict:
    """Mapa lowercase-stem -> sciezka, z folderu wejscia i textures/."""
    index: dict[str, Path] = {}
    dirs = [base_dir, base_dir / "textures", base_dir / "Textures"]
    for d in dirs:
        if not d.is_dir():
            continue
        for f in d.iterdir():
            if f.suffix.lower() in IMG_EXTS:
                index.setdefault(f.stem.lower(), f)
    return index


def _open(path: Path, log: PipelineLog) -> Image.Image | None:
    try:
        return Image.open(path).convert("RGBA")
    except OSError as e:
        log.warn(f"tekstura {path.name}: {e}")
        return None


def _assign(mat: MaterialData, key: str, img: Image.Image,
            src_name: str, log: PipelineLog, forced: bool) -> None:
    slot_attr = {
        "basecolor": "base_color_tex", "normal": "normal_tex",
        "occlusion": "occlusion_tex", "roughness": "roughness_tex",
        "metallic": "metallic_tex", "emissive": "emissive_tex",
        "opacity": "opacity_tex",
    }
    if key == "orm":
        # packed: wszystkie trzy kanaly z jednego obrazka (ten sam obiekt
        # = importer wie, ze R/G/B sa rozdzielone)
        if forced or (mat.occlusion_tex is None and mat.roughness_tex is None
                      and mat.metallic_tex is None):
            mat.occlusion_tex = img
            mat.roughness_tex = img
            mat.metallic_tex = img
            mat.roughness_factor = 1.0
            mat.metallic_factor = 1.0
            log.info(f"  {mat.name}: ORM <- {src_name}")
        return
    if key == "gloss":
        if forced or mat.roughness_tex is None:
            gray = ImageOps.invert(img.convert("L")).convert("RGBA")
            mat.roughness_tex = gray
            log.info(f"  {mat.name}: roughness <- {src_name} (odwrocony gloss)")
        return
    attr = slot_attr[key]
    if forced or getattr(mat, attr) is None:
        setattr(mat, attr, img)
        # factory z pliku nie moga tlumic jawnie przypisanej mapy
        # (UE-FBX trzyma czesto ciemne/zerowe diffuse factory)
        if key == "basecolor":
            a = mat.base_color_factor[3] if len(mat.base_color_factor) > 3 else 1.0
            mat.base_color_factor = (1.0, 1.0, 1.0, a)
        elif key == "roughness":
            mat.roughness_factor = 1.0
        elif key == "metallic":
            mat.metallic_factor = 1.0
        elif key == "emissive":
            mat.emissive_factor = (1.0, 1.0, 1.0)
        elif key == "opacity":
            mat.opacity_factor = 1.0
        log.info(f"  {mat.name}: {key} <- {src_name}")


def _apply_sidecar_entry(mat: MaterialData, entry: dict, base_dir: Path,
                         log: PipelineLog) -> None:
    for key in SUFFIXES:
        raw = entry.get(key)
        if not raw:
            continue
        p = Path(raw)
        if not p.is_absolute():
            p = base_dir / p
        if not p.exists():
            log.warn(f"  {mat.name}: sidecar {key} nie istnieje: {raw}")
            continue
        img = _open(p, log)
        if img is not None:
            _assign(mat, key, img, p.name, log, forced=True)


def _apply_name_convention(mat: MaterialData, index: dict,
                           asset_name: str, log: PipelineLog) -> None:
    import re as _re
    stem = mat.name.lower()
    # Substance Painter: znaki specjalne w nazwie materialu -> '_'
    sanitized = _re.sub(r"[^\w\- ]", "_", stem)
    variants = []
    for base in dict.fromkeys([stem, sanitized]):  # unikalne, kolejnosc
        variants.append(base)
        # Substance prefiksuje nazwa mesha/assetu: <asset>_<material>_Mapa
        variants.append(f"{asset_name.lower()}_{base}")
        # MI_/M_ prefiksy z UE — probujemy tez bez nich i z T_ zamiast
        for pref in ("mi_", "m_"):
            if base.startswith(pref):
                variants.append(base[len(pref):])
                variants.append("t_" + base[len(pref):])
    for key, sufs in SUFFIXES.items():
        for var in variants:
            hit = None
            for s in sufs:
                hit = index.get(f"{var}_{s}")
                if hit is not None:
                    break
            if hit is None:
                continue
            img = _open(hit, log)
            if img is not None:
                _assign(mat, key, img, hit.name, log, forced=False)
            break
