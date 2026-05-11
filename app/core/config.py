import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass
class SettingsData:
    min_spread: float = 7.0
    entry_offset: float = 1.0
    exit_offset: float = 1.0
    target_capture: float = 5.0
    stop_loss: float = 12.0
    max_hold_ms: int = 20000
    lot_size: float = 0.001
    max_open_lots: int = 1
    max_daily_loss: float = 200.0
    max_exposure_u: float = 1000.0
    live_max_exposure_u: float = 20.0
    panic_exit: bool = True
    live_enabled: bool = False
    require_confirmation: bool = True
    arm_live: bool = False
    auto_cancel_on_stop: bool = True
    entry_timeout_ms: int = 10000
    exit_timeout_ms: int = 20000
    panic_reprice_once: bool = True
    rest_poll_ms: int = 2000
    ws_optional_enabled: bool = True
    max_ws_age_ms: int = 5000
    ui_theme: str = "dark"


@dataclass(frozen=True)
class UBConfig:
    display_symbol: str = "BTC/U"
    binance_symbol: str = "BTCU"
    stream_symbol: str = "btcu"
    tick_size_default: float = 0.01


class SettingsStore:
    def __init__(self, path: str = "config/settings.json") -> None:
        self.path = Path(path)

    def load(self) -> SettingsData:
        if not self.path.exists():
            data = SettingsData()
            self.save(data)
            return data
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        current = asdict(SettingsData())
        current.update(payload)
        return SettingsData(**current)

    def save(self, data: SettingsData) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(asdict(data), indent=2), encoding="utf-8")


CONFIG = UBConfig()
SETTINGS_STORE = SettingsStore()
