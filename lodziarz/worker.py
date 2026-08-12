"""Uruchamianie pipeline'u w osobnym procesie.

Natywne crashe (access violation w ufbx / GL / FBX SDK) zabijaja tylko
proces roboczy — GUI i batch przezywaja i raportuja czytelny blad.
Log plynie przez Queue na zywo.
"""
from __future__ import annotations

import faulthandler
import multiprocessing as mp
import queue as queue_mod
from dataclasses import asdict
from pathlib import Path

from .logutil import PipelineLog
from .pipeline import ProcessOptions, ProcessResult


class _QueueLog(PipelineLog):
    """PipelineLog przekazujacy linie do rodzica przez Queue."""

    def __init__(self, q):
        super().__init__(echo=False)
        self.q = q

    def _emit(self, level: str, msg: str) -> None:
        super()._emit(level, msg)
        self.q.put(("log", self.lines[-1]))

    def progress(self, percent: float, stage: str) -> None:
        super().progress(percent, stage)
        self.q.put(("prog", float(percent), stage))


def _worker_main(q, input_path: str, out_dir: str, opts_dict: dict) -> None:
    # crash log natywny — obok wyniku
    try:
        crash_dir = Path(out_dir)
        crash_dir.mkdir(parents=True, exist_ok=True)
        crash_file = open(crash_dir / "lodziarz_crash.log", "w", encoding="utf-8")
        faulthandler.enable(file=crash_file)
    except OSError:
        pass
    from .pipeline import process_asset
    log = _QueueLog(q)
    result = process_asset(input_path, out_dir, ProcessOptions(**opts_dict), log)
    q.put(("result", asdict(result)))


def run_isolated(input_path: str | Path, out_dir: str | Path,
                 opts: ProcessOptions, log: PipelineLog) -> ProcessResult:
    """Odpala process_asset w subprocesie; log/progress przechodza do `log`."""
    ctx = mp.get_context("spawn")
    q = ctx.Queue()
    proc = ctx.Process(target=_worker_main,
                       args=(q, str(input_path), str(out_dir), asdict(opts)),
                       daemon=True)
    proc.start()
    result_dict = None
    while True:
        try:
            kind, *payload = q.get(timeout=0.5)
        except queue_mod.Empty:
            if not proc.is_alive():
                break
            continue
        if kind == "log":
            log.lines.append(payload[0])
            if log.echo:
                print(payload[0], flush=True)
        elif kind == "prog":
            log.percent, log.stage = payload[0], payload[1]
        elif kind == "result":
            result_dict = payload[0]
    proc.join(timeout=10)

    if result_dict is not None:
        return ProcessResult(**result_dict)
    # worker padl bez wyniku = crash natywny
    code = proc.exitcode
    log.error(f"proces roboczy padl (exit code {code}) — crash natywny "
              f"podczas etapu: '{log.stage or 'import'}'")
    log.error(f"szczegoly (jesli sa): {Path(out_dir) / 'lodziarz_crash.log'}")
    log.error("prawdopodobna przyczyna: nietypowy plik wejsciowy; zglos plik "
              "do debugowania")
    return ProcessResult(ok=False, error=f"crash natywny (exit {code}), "
                                         f"etap: {log.stage or 'import'}")
