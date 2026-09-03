"""CLI — te same operacje co GUI, do batch processingu."""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from .importer import SUPPORTED
from .logutil import PipelineLog
from .pipeline import ProcessOptions
from .presets import PRESETS
from .worker import run_isolated


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="lodziarz",
        description="Game-ready assety: LOD-y + bake atlasu + export FBX/GLB")
    sub = p.add_subparsers(dest="command")

    pr = sub.add_parser("process", help="przetworz plik(i)")
    pr.add_argument("input", nargs="+",
                    help="pliki wejsciowe (FBX/OBJ/glTF/GLB) lub katalog")
    pr.add_argument("-o", "--out", required=True, help="katalog wyjsciowy")
    pr.add_argument("--preset", default=None, choices=sorted(PRESETS),
                    help="zestaw domyslnych opcji pod target: unreal (FBX "
                         "LODGroup, DX, packed ORM), unity (FBX plaski, GL, "
                         "gloss osobno), godot (tylko GLB, GL), max (FBX per "
                         "LOD, embed), loose (mapy osobno, FBX per LOD); "
                         "jawne flagi nadpisuja preset")
    pr.add_argument("--lods", type=int, default=4, help="liczba LOD-ow (default 4)")
    pr.add_argument("--ratio", type=float, default=0.5,
                    help="ratio trojkatow na LOD (default 0.5)")
    pr.add_argument("--smooth-weld", action="store_true",
                    help="sklej hard edges przed simplify (LOD1+): wiecej "
                         "redukcji na hard-surface, miekksze cieniowanie "
                         "krawedzi; LOD0 bez zmian")
    pr.add_argument("--thresholds", default=None,
                    help="progi LODGroup w cm, np. '500,1000,2000' "
                         "(default: auto wg rozmiaru obiektu)")
    pr.add_argument("--no-bake", action="store_true",
                    help="bez scalania materialow / bake atlasu")
    pr.add_argument("--bake-lods", default=None,
                    help="ktore LOD-y dostaja atlas, np. '1,2,3' albo 'all' "
                         "(default: wszystkie oprocz LOD0)")
    pr.add_argument("--backend", default="texel",
                    choices=["texel", "raycast", "cameras26"],
                    help="backend bake (default texel)")
    pr.add_argument("--atlas", type=int, default=1024,
                    help="rozdzielczosc atlasu 512-4096 (default 1024)")
    pr.add_argument("--dilation", type=int, default=8,
                    help="padding wysp w px (default 8)")
    pr.add_argument("--ssaa", type=int, default=2, choices=[1, 2, 4],
                    help="antyaliasing bake: 1=off, 2, 4 (default 2)")
    pr.add_argument("--cage-offset", type=float, default=0.0,
                    help="raycast: inflacja cage w metrach (0 = auto 1%% diag)")
    pr.add_argument("--tga", action="store_true", help="tekstury TGA zamiast PNG")
    pr.add_argument("--split-orm", action="store_true",
                    help="AO/Roughness/Metallic osobno zamiast jednego ORM")
    pr.add_argument("--gloss", action="store_true",
                    help="przy --split-orm: glossiness zamiast roughness")
    pr.add_argument("--normal-gl", action="store_true",
                    help="zapisz normal mape w konwencji OpenGL "
                         "(default: DirectX)")
    pr.add_argument("--input-normal-gl", action="store_true",
                    help="wejsciowe normalki sa OpenGL (default: DirectX)")
    pr.add_argument("--per-lod-fbx", action="store_true",
                    help="dodatkowo kazdy LOD osobnym plikiem SM_*_LODn.fbx")
    pr.add_argument("--flat-fbx", action="store_true",
                    help="FBX bez node'a FbxLODGroup — dzieci *_LOD0..N "
                         "(konwencja nazw Unity)")
    pr.add_argument("--embed-textures", action="store_true",
                    help="wbuduj tekstury do pliku FBX")
    pr.add_argument("--no-fbx", action="store_true", help="bez exportu FBX")
    pr.add_argument("--no-glb", action="store_true", help="bez exportu GLB")
    pr.add_argument("--obj", action="store_true",
                    help="dodatkowo export OBJ (per LOD + wspolny MTL)")
    pr.add_argument("--up", default="auto", choices=["auto", "y", "z"],
                    help="orientacja zrodla: auto (z pliku), y (Maya/Unity), "
                         "z (UE/3ds Max)")

    g = sub.add_parser("gui", help="uruchom GUI z viewerem (default)")
    g.add_argument("--browser", action="store_true",
                   help="otworz w przegladarce zamiast okna pywebview")
    return p


def _collect_inputs(inputs: list[str]) -> list[Path]:
    files = []
    for raw in inputs:
        p = Path(raw)
        if p.is_dir():
            for ext in SUPPORTED:
                files.extend(sorted(p.glob(f"*{ext}")))
        elif p.exists():
            files.append(p)
        else:
            print(f"POMINIETO (nie istnieje): {p}", file=sys.stderr)
    return files


def _parse_thresholds(raw):
    if raw is None or not str(raw).strip():
        return None
    try:
        vals = [float(x) for x in str(raw).replace(";", ",").split(",")
                if x.strip()]
    except ValueError:
        print(f"zly format --thresholds '{raw}' (np. '500,1000,2000')",
              file=sys.stderr)
        sys.exit(2)
    return vals or None


def _parse_bake_lods(raw, count: int):
    if raw is None:
        return None
    raw = str(raw).strip().lower()
    if raw == "all":
        return list(range(count))
    if raw in ("none", ""):
        return []
    try:
        return sorted({int(x) for x in raw.split(",") if x.strip() != ""})
    except ValueError:
        print(f"zly format --bake-lods '{raw}' (np. '1,2,3' albo 'all')",
              file=sys.stderr)
        sys.exit(2)


def run_process(args) -> int:
    lod_count = max(1, args.lods)
    # preset = baza dla pol formatu/konwencji; jawna flaga CLI nadpisuje
    # (flagi sa "wlaczajace" — brak flagi zostawia wartosc presetu)
    kw = dict(PRESETS[args.preset]) if args.preset else {}
    if args.normal_gl:
        kw["output_normal_directx"] = False
    if args.split_orm:
        kw["orm_split"] = True
    if args.gloss:
        kw["output_gloss"] = True
    if args.per_lod_fbx:
        kw["fbx_per_lod"] = True
    if args.flat_fbx:
        kw["fbx_flat_lods"] = True
    if args.embed_textures:
        kw["fbx_embed_textures"] = True
    if args.no_fbx:
        kw["export_fbx"] = False
    if args.no_glb:
        kw["export_glb"] = False
    if args.obj:
        kw["export_obj"] = True
    opts = ProcessOptions(
        lod_count=lod_count,
        lod_ratio=min(0.95, max(0.05, args.ratio)),
        smooth_weld=args.smooth_weld,
        lod_thresholds=_parse_thresholds(args.thresholds),
        bake=not args.no_bake,
        baked_lods=_parse_bake_lods(args.bake_lods, lod_count),
        bake_backend=args.backend,
        atlas_resolution=min(4096, max(512, args.atlas)),
        dilation=max(0, args.dilation),
        ssaa=args.ssaa,
        cage_offset=max(0.0, args.cage_offset),
        input_normal_directx=not args.input_normal_gl,
        texture_format="tga" if args.tga else "png",
        up_axis=args.up,
        **kw,
    )
    files = _collect_inputs(args.input)
    if not files:
        print("brak plikow wejsciowych", file=sys.stderr)
        return 2
    out_root = Path(args.out)
    failed = 0
    batch: list[dict] = []
    for f in files:
        log = PipelineLog(echo=True)
        log.info(f"=== {f.name} ===")
        out_dir = out_root / f.stem if len(files) > 1 else out_root
        # subprocess per plik: crash natywny nie zabija calego batcha
        result = run_isolated(f, out_dir, opts, log)
        if not result.ok:
            failed += 1
        batch.append({"input": str(f), **asdict(result)})
    if len(files) > 1:
        _write_batch_report(out_root, batch)
    if failed:
        print(f"\nBLEDY: {failed}/{len(files)} plikow nie przeszlo",
              file=sys.stderr)
    return 1 if failed else 0


def _write_batch_report(out_root: Path, batch: list[dict]) -> None:
    """Raport zbiorczy batcha: pelny JSON + skrotowy CSV."""
    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / "lodziarz_batch_report.json").write_text(
        json.dumps(batch, indent=2), encoding="utf-8")
    lines = ["input;ok;lod_tris;materials;warnings;error"]
    for r in batch:
        tris = "/".join(str(s.get("tris", "")) for s in r.get("lod_stats", []))
        val = r.get("validation") or {}
        lines.append(";".join([
            Path(r["input"]).name,
            "1" if r.get("ok") else "0",
            tris,
            str(val.get("materials", "")),
            str(len(val.get("warnings", []))),
            str(r.get("error", "")).replace(";", ","),
        ]))
    (out_root / "lodziarz_batch_report.csv").write_text(
        "\n".join(lines) + "\n", encoding="utf-8")
    print(f"raport zbiorczy: {out_root / 'lodziarz_batch_report.json'} + .csv")


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "process":
        return run_process(args)
    # default: GUI
    from .viewer.server import run_gui
    return run_gui(browser=getattr(args, "browser", False))


if __name__ == "__main__":
    sys.exit(main())
