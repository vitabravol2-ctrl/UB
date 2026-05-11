from __future__ import annotations

import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any


class GridCrashLogger:
    def __init__(self, base_dir: str = "logs/grid_sessions") -> None:
        self.base_dir = Path(base_dir)
        self.last_gui_action = "N/A"
        self.last_runtime_state = "N/A"
        self.last_market_snapshot: Any = "N/A"
        self.last_settings: Any = "N/A"
        self.last_grid_summary: Any = "N/A"

    def install(self) -> None:
        sys.excepthook = self._handle_exception

    def _handle_exception(self, exc_type: type[BaseException], exc_value: BaseException, exc_tb: Any) -> None:
        ts = datetime.utcnow().strftime("%Y-%m-%d_%H-%M-%S")
        path = self.base_dir / f"grid_crash_{ts}.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        trace = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        payload = (
            f"exception_type={exc_type.__name__}\n"
            f"exception={exc_value}\n"
            f"last_gui_action={self.last_gui_action}\n"
            f"last_runtime_state={self.last_runtime_state}\n"
            f"last_market_snapshot={self.last_market_snapshot}\n"
            f"last_settings={self.last_settings}\n"
            f"last_grid_summary={self.last_grid_summary}\n\n"
            f"traceback:\n{trace}"
        )
        path.write_text(payload, encoding="utf-8")
