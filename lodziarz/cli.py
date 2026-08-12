"""CLI — te same operacje co GUI, do batch processingu."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .importer import SUPPORTED
from .logutil import PipelineLog
from .pipeline import ProcessOptions, process_asset


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="lodziarz",
        description="Game-ready assety: LOD-y + bake atlasu + export FBX/GLB")
    sub = p.add_subparsers(dest="command")

    pr = sub.add_parser("process", help="przetworz plik(i)")
    pr.add_argument("input", nargs="+",
                    help="pliki wejsciowe (FBX/OBJ/glTF/GLB) lub katalog")
    pr.add_argument("-o", "--out", required=True, help="katalog wyjsciowy")
    pr.add_argument("--lods", type=int, default=4, help="liczba LOD-ow (default 4)")
    pr.add_argument("--ratio", type=float, default=0.5,
                    help="ratio trojkatow na LOD (default 0.5)")
    pr.add_argument("--no-bake", action="store_true",
                    help="bez scalania materialow / bake atlasu")
    pr.add_argument("--backend", default="texel",
                    choices=["texel", "raycast", "cameras26"],
                    help="backend bake (default texel)")
    pr.add_argument("--atlas", type=int, default=2048,
                    help="rozdzielczosc atlasu 512-4096 (default 2048)")
    pr.add_argument("--dilation", type=int, default=8,
                    help="padding wysp w px (default 8)")
    pr.add_argument("--ssaa", type=int, default=2, choices=[1, 2, 4],
                    help="antyaliasing bake: 1=off, 2, 4 (default 2)")
    pr.add_argument("--tga", action="store_true", help="tekstury TGA zamiast PNG")
    pr.add_argument("--normal-dx", action="store_true",
                    help="zapisz normal mape w konwencji DirectX (flip G)")
    pr.add_argument("--input-normal-dx", action="store_true",
                    help="wejsciowe normalki sa DirectX")
    pr.add_argument("--per-lod-fbx", action="store_true",
                    help="dodatkowo kazdy LOD osobnym plikiem SM_*_LODn.fbx")
    pr.add_argument("--no-glb", action="store_true", help="bez exportu GLB")

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


def run_process(args) -> int:
    opts = ProcessOptions(
        lod_count=max(1, args.lods),
        lod_ratio=min(0.95, max(0.05, args.ratio)),
        bake=not args.no_bake,
        bake_backend=args.backend,
        atlas_resolution=min(4096, max(512, args.atlas)),
        dilation=max(0, args.dilation),
        ssaa=args.ssaa,
        input_normal_directx=args.input_normal_dx,
        output_normal_directx=args.normal_dx,
        texture_format="tga" if args.tga else "png",
        fbx_per_lod=args.per_lod_fbx,
        export_glb=not args.no_glb,
    )
    files = _collect_inputs(args.input)
    if not files:
        print("brak plikow wejsciowych", file=sys.stderr)
        return 2
    out_root = Path(args.out)
    failed = 0
    for f in files:
        log = PipelineLog(echo=True)
        log.info(f"=== {f.name} ===")
        out_dir = out_root / f.stem if len(files) > 1 else out_root
        result = process_asset(f, out_dir, opts, log)
        if not result.ok:
            failed += 1
    if failed:
        print(f"\nBLEDY: {failed}/{len(files)} plikow nie przeszlo",
              file=sys.stderr)
    return 1 if failed else 0


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "process":
        return run_process(args)
    # default: GUI
    from .viewer.server import run_gui
    return run_gui(browser=getattr(args, "browser", False))


if __name__ == "__main__":
    sys.exit(main())
