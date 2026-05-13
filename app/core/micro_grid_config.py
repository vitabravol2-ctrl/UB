from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class MicroGridSettings:
    symbol: str = "BTCU"
    grid_step_u: float = 5.0
    buy_levels_down: int = 25
    sell_levels_up: int = 25
    investment_u: float = 10000.0
    budget_buy_side_u: float = 5000.0
    budget_sell_side_u: float = 5000.0
    max_live_buy_orders: int = 25
    max_live_sell_orders: int = 25
    auto_reinvest: bool = True
    dry_run: bool = False
    live_enabled: bool = True
    test_order_limit: int = 0
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
