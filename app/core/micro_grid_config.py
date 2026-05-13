from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class MicroGridSettings:
    symbol: str = "BTCU"
    lower_price: float = 80000.0
    upper_price: float = 81000.0
    grid_count: int = 50
    investment_u: float = 10000.0
    max_active_orders: int = 10
    auto_reinvest: bool = True
    dry_run: bool = False
    live_enabled: bool = True
    cancel_on_stop: bool = True


class MicroGridSettingsStore:
    def __init__(self, path: str = "config/micro_grid_settings.json") -> None:
        self.path = Path(path)

    def load(self) -> MicroGridSettings:
        if not self.path.exists():
            data = MicroGridSettings()
            self.save(data)
            return data
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        data = asdict(MicroGridSettings())
        for k in data:
            if k in payload:
                data[k] = payload[k]
        return MicroGridSettings(**data)

    def save(self, settings: MicroGridSettings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(asdict(settings), indent=2), encoding="utf-8")


MICRO_GRID_SETTINGS_STORE = MicroGridSettingsStore()
