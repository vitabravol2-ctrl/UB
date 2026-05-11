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
    order_size_u: float = 20.0
    max_open_lots: int = 1
    max_daily_loss: float = 200.0
    max_exposure_u: float = 20.0
    max_live_exposure_u: float = 20.0
    panic_exit: bool = True
    live_enabled: bool = False
    require_confirmation: bool = True
    auto_cancel_on_stop: bool = True
    buy_timeout_ms: int = 4500
    buy_timeout_ms_fast: int = 1200
    entry_mode: str = "BALANCED"
    entry_reprice_enabled: bool = True
    entry_reprice_cooldown_ms: int = 300
    max_entry_reprices: int = 3
    entry_chase_ticks: int = 1
    entry_cross_if_spread_ticks_above: int = 1000
    min_spread_after_entry_ticks: int = 3
    sell_timeout_ms: int = 5000
    sell_reprice_cooldown_ms: int = 700
    panic_reprice_once: bool = True
    aggressive_exit_offset: float = 1.0
    max_sell_reprices: int = 2
    min_profit_ticks: int = 1
    take_profit_ticks: int = 3
    stop_loss_ticks: int = 6
    rest_poll_ms: int = 2000
    open_orders_poll_ms: int = 2000
    all_orders_poll_ms: int = 9000
    balances_poll_ms: int = 10000
    active_order_poll_ms: int = 800
    debug_api_logs: bool = False
    ws_optional_enabled: bool = True
    max_ws_age_ms: int = 5000
    ui_theme: str = "dark"
    guard_mode: str = "BALANCED"
    guard_enabled: bool = True
    require_ws_for_buy: bool = True
    max_ws_age_for_buy_ms: int = 1500
    min_spread_lifetime_ms: int = 300
    stable_snapshots_required: int = 3
    stable_snapshot_window_ms: int = 1000
    max_negative_mid_delta: float = -3.0
    max_negative_bid_delta: float = -5.0
    block_on_mid_negative: bool = True
    block_on_bid_unstable: bool = True
    block_on_snapshots_insufficient: bool = True
    loss_cooldown_ms: int = 2000
    panic_cooldown_ms: int = 3000
    balance_safety_buffer_u: float = 10.0
    block_log_throttle_ms: int = 2000
    health_log_throttle_ms: int = 2000


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
        if "lot_size" in payload and "order_size_u" not in payload:
            legacy_lot = float(payload.get("lot_size", 0.0) or 0.0)
            payload["order_size_u"] = legacy_lot if legacy_lot > 1 else 20.0
            payload["legacy_qty_btc"] = legacy_lot if legacy_lot < 1 else 0.0
        if "live_max_exposure_u" in payload and "max_live_exposure_u" not in payload:
            payload["max_live_exposure_u"] = float(payload["live_max_exposure_u"])
        current.update(payload)
        current.pop("lot_size", None)
        current.pop("legacy_qty_btc", None)
        if "entry_timeout_ms" in payload and "buy_timeout_ms" not in payload:
            current["buy_timeout_ms"] = int(payload.get("entry_timeout_ms") or 5000)
        if "exit_timeout_ms" in payload and "sell_timeout_ms" not in payload:
            current["sell_timeout_ms"] = int(payload.get("exit_timeout_ms") or 7000)
        current.pop("entry_timeout_ms", None)
        current.pop("exit_timeout_ms", None)
        current.pop("live_max_exposure_u", None)
        current.pop("arm_live", None)
        return SettingsData(**current)

    def save(self, data: SettingsData) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(asdict(data), indent=2), encoding="utf-8")


    @staticmethod
    def _sanitize_payload(payload: dict[str, Any], include_secrets: bool = True) -> dict[str, Any]:
        clean = dict(payload)
        if not include_secrets:
            for key in list(clean):
                low = key.lower()
                if "secret" in low or "api_key" in low or low.endswith("key"):
                    clean.pop(key, None)
        return clean

    def export_settings_json(self, export_path: str) -> None:
        data = asdict(self.load())
        safe = self._sanitize_payload(data, include_secrets=False)
        Path(export_path).write_text(json.dumps(safe, indent=2), encoding="utf-8")

    def import_settings_json(self, import_path: str) -> SettingsData:
        payload = json.loads(Path(import_path).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("settings payload must be JSON object")
        base = asdict(self.load())
        merged = dict(base)
        for key, value in payload.items():
            if key not in base:
                continue
            old = base[key]
            if isinstance(old, bool):
                if isinstance(value, bool):
                    merged[key] = value
                else:
                    raise ValueError(f"invalid bool for {key}")
            elif isinstance(old, int):
                merged[key] = int(value)
            elif isinstance(old, float):
                merged[key] = float(value)
            elif isinstance(old, str):
                merged[key] = str(value)
            else:
                merged[key] = value
        data = SettingsData(**merged)
        self.save(data)
        return data


CONFIG = UBConfig()
SETTINGS_STORE = SettingsStore()
