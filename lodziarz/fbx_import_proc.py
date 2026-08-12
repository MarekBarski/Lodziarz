"""Import FBX w dedykowanym podprocesie.

Binding ufbx 0.0.5 psuje sterte procesu (AV w losowych miejscach po sesji:
GC, lazy importy C-extensions, teardown). Jedyny pewny sposob to pelna
izolacja: podproces laduje FBX, wysyla gotowy Asset (numpy + PIL pikluja
sie normalnie) i konczy przez os._exit(0) — zero teardownu, zero szansy
na AV w naszym procesie.
"""
from __future__ import annotations

import multiprocessing as mp
import os
import queue as queue_mod
from pathlib import Path

from .core import Asset
from .logutil import PipelineLog


def _fbx_child_main(q, path: str) -> None:
    log = PipelineLog(echo=False)
    try:
        from .importer import _load_fbx
        asset = _load_fbx(Path(path), log)
        q.put(("ok", asset, log.lines))
    except Exception as e:  # czysty blad (np. zly plik) — nie crash
        q.put(("error", f"{type(e).__name__}: {e}", log.lines))
    q.close()
    q.join_thread()          # dopchnij dane do rury zanim zabijemy proces
    os._exit(0)              # bez teardownu — ufbx nie dostaje szansy na AV


def load_fbx_isolated(path: Path, log: PipelineLog,
                      timeout: float = 600.0) -> Asset:
    ctx = mp.get_context("spawn")
    q = ctx.Queue()
    proc = ctx.Process(target=_fbx_child_main, args=(q, str(path)),
                       daemon=False)
    proc.start()
    payload = None
    waited = 0.0
    while payload is None:
        try:
            payload = q.get(timeout=0.5)
        except queue_mod.Empty:
            waited += 0.5
            if not proc.is_alive():
                break
            if waited > timeout:
                proc.kill()
                raise ValueError(f"import FBX przekroczyl {timeout:.0f}s — przerwano")
    proc.join(timeout=5)

    if payload is None:
        raise ValueError(
            f"import FBX padl natywnie (exit code {proc.exitcode}) — "
            f"plik nieobslugiwany przez ufbx, zglos do debugowania")
    status, data, lines = payload
    log.lines.extend(lines)
    if log.echo:
        for line in lines:
            print(line, flush=True)
    if status == "error":
        raise ValueError(data)
    return data
