"""Logger pipeline'u — konsola + kolejka dla GUI."""
from __future__ import annotations

import time


class PipelineLog:
    def __init__(self, echo: bool = True):
        self.lines: list[str] = []
        self.echo = echo
        self.percent = 0.0
        self.stage = ""

    def _emit(self, level: str, msg: str) -> None:
        line = f"[{time.strftime('%H:%M:%S')}] {level:5s} {msg}"
        self.lines.append(line)
        if self.echo:
            print(line, flush=True)

    def info(self, msg: str) -> None:
        self._emit("INFO", msg)

    def warn(self, msg: str) -> None:
        self._emit("WARN", msg)

    def error(self, msg: str) -> None:
        self._emit("ERROR", msg)

    def progress(self, percent: float, stage: str) -> None:
        self.percent = float(percent)
        self.stage = stage
        self._emit("PROG", f"{percent:5.1f}%  {stage}")
