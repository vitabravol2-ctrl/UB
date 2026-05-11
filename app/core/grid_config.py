import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class GridSettings:
    upper_price: float = 82000.0
    lower_price: float = 81000.0
    budget_u: float = 10000.0
    levels: int = 20
    profit_ticks: int = 100
    max_exposure_u: float = 10000.0
    auto_float_enabled: bool = False
    live_enabled: bool = False


class GridSettingsStore:
    def __init__(self, path: str = "config/grid_settings.json") -> None:
        self.path = Path(path)

    def load(self) -> GridSettings:
        if not self.path.exists():
            defaults = GridSettings()
            self.save(defaults)
            return defaults
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        data = asdict(GridSettings())
        if isinstance(payload, dict):
            data.update(payload)
        return GridSettings(**data)

    def save(self, settings: GridSettings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(asdict(settings), indent=2), encoding="utf-8")


GRID_SETTINGS_STORE = GridSettingsStore()
