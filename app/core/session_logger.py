from __future__ import annotations

from collections import deque
from datetime import datetime
from pathlib import Path
from threading import Lock


class SessionLogger:
    def __init__(self, symbol: str = "BTCU", base_dir: str = "logs/grid_sessions") -> None:
        ts = datetime.utcnow().strftime("%Y-%m-%d_%H-%M-%S")
        self.path = Path(base_dir) / f"grid_{ts}_{symbol}.log"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self.gui_lines: deque[str] = deque(maxlen=300)

    def log(self, tag: str, message: str) -> str:
        now = datetime.utcnow().strftime("%H:%M:%S")
        line = f"[{now}] [{tag}] {message}"
        with self._lock:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
                f.flush()
            self.gui_lines.append(line)
        return line

    def append_gui_line(self, message: str) -> str:
        return self.log("GRID", message)

    def get_gui_lines(self) -> list[str]:
        return list(self.gui_lines)
