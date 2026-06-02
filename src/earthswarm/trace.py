from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


TraceFn = Callable[[str], None]


@dataclass
class RunTracer:
    enabled: bool = True
    sink: TraceFn = print
    prefix: str = "[openearth]"
    started_at: float = field(default_factory=time.perf_counter)

    def emit(self, message: str) -> None:
        if not self.enabled:
            return
        elapsed = time.perf_counter() - self.started_at
        self.sink(f"{self.prefix} {elapsed:7.1f}s  {message}")

    def path(self, label: str, path: str | Path) -> None:
        self.emit(f"{label}: {Path(path)}")


def make_tracer(enabled: bool = True) -> RunTracer:
    return RunTracer(enabled=enabled)
