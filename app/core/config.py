import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass
class SettingsData:
    min_spread: float = 7.0
    min_spread_ticks: int = 6
    entry_offset: float = 1.0
    entry_offset_ticks: int = 1
    exit_offset: float = 0.5
    exit_offset_ticks: int = 1
    target_capture: float = 3.0
    target_capture_ticks: int = 3
    stop_loss: float = 6.0
    stop_loss_ticks: int = 220
    max_hold_ms: int = 3500
    order_size_u: float = 223.0
    max_open_lots: int = 1
    max_daily_loss: float = 100.0
    max_exposure_u: float = 5000.0
    max_live_exposure_u: float = 5000.0
    panic_exit: bool = True
    live_enabled: bool = True
    require_confirmation: bool = False
    auto_cancel_on_stop: bool = True
    buy_timeout_ms: int = 1400
    buy_timeout_ms_fast: int = 500
    buy_watchdog_ms: int = 1400
    far_buy_ticks: int = 18
    entry_mode: str = "AGGRESSIVE"
    entry_reprice_enabled: bool = True
    entry_reprice_cooldown_ms: int = 120
    max_entry_reprices: int = 8
    entry_chase_ticks: int = 2
    entry_cross_if_spread_ticks_above: int = 300
    min_spread_after_entry_ticks: int = 1
    sell_timeout_ms: int = 2200
    sell_watchdog_ms: int = 1800
    place_sell_stuck_ms: int = 1500
    far_sell_ticks: int = 30
    sell_floor_hold_enabled: bool = True
    sell_floor_hold_max_ms: int = 12000
    smart_exit_enabled: bool = True
    protected_hold_soft_ms: int = 5000
    protected_hold_hard_ms: int = 12000
    protected_hold_max_dist_ticks: int = 1200
    protected_sell_ignore_log_throttle_ms: int = 5000
    panic_far_sell_multiplier: int = 5
    sell_reprice_cooldown_ms: int = 120
    place_sell_recovery_cooldown_ms: int = 2500
    panic_reprice_once: bool = False
    aggressive_exit_offset: float = -0.5
    max_sell_reprices: int = 12
    exit_engine_enabled: bool = True
    exit_stage1_ms: int = 400
    exit_stage2_ms: int = 900
    exit_stage3_ms: int = 1400
    exit_reprice_step_ticks: int = 1
    exit_max_reprices: int = 14
    panic_ladder_enabled: bool = True
    panic_ladder_step_ticks: int = 1
    panic_ladder_ms: int = 180
    panic_hold_max_ms: int = 1200
    max_smart_exit_attempts: int = 8
    panic_cross_after_ms: int = 1600
    taker_exit_enabled: bool = True
    taker_exit_after_ms: int = 1600
    taker_exit_ioc: bool = True
    taker_exit_spread_collapse_ticks: int = 4
    taker_exit_mid_negative_threshold: float = -80.0
    taker_exit_min_expected_profit_ticks: int = 0
    taker_exit_max_slippage_ticks: int = 2
    taker_exit_force_flat_after_ms: int = 1600
    taker_status_poll_ms: int = 300
    taker_status_timeout_ms: int = 1200
    exit_ioc_enabled: bool = False
    min_profit_ticks: int = 1
    take_profit_ticks: int = 2
    rest_poll_ms: int = 700
    open_orders_poll_ms: int = 250
    all_orders_poll_ms: int = 9000
    balances_poll_ms: int = 6000
    active_order_poll_ms: int = 100
    debug_api_logs: bool = False
    ws_optional_enabled: bool = True
    max_ws_age_ms: int = 6000
    ui_theme: str = "dark"
    guard_mode: str = "FAST"
    guard_enabled: bool = True
    require_ws_for_buy: bool = False
    max_ws_age_for_buy_ms: int = 2000
    min_spread_lifetime_ms: int = 0
    stable_snapshots_required: int = 1
    stable_snapshot_window_ms: int = 500
    max_negative_mid_delta: float = -50.0
    max_negative_bid_delta: float = -80.0
    block_on_mid_negative: bool = False
    block_on_bid_unstable: bool = False
    block_on_snapshots_insufficient: bool = False
    loss_cooldown_ms: int = 800
    panic_cooldown_ms: int = 800
    balance_safety_buffer_u: float = 10.0
    block_log_throttle_ms: int = 2000
    health_log_throttle_ms: int = 2000
    manual_stop_on_blocked_exit: bool = True
    exit_block_manual_enabled: bool = True
    inventory_epsilon_qty: float = 0.000001
    min_sellable_qty_fallback: float = 0.0001
    micro_partial_reconcile_enabled: bool = True
    micro_partial_max_qty: float = 0.0001
    dust_cleanup_enabled: bool = True
    dust_cleanup_threshold_qty: float = 0.0
    sell_qty_clamp_log_throttle_ms: int = 2000
    exit_recovery_log_throttle_ms: int = 2000
    compact_logs: bool = True
    runtime_diag_enabled: bool = True
    gui_log_mode: str = "OFF"
    gui_logs_visible_default: bool = False
    micro_grid_enabled: bool = False
    micro_grid_size_ticks: int = 400
    micro_grid_step_ticks: int = 10
    micro_grid_budget_u: float = 5000.0
    stream_count: int = 10
    stream_range_ticks: int = 1200
    stream_max_active_buys: int = 2
    stream_place_batch_size: int = 1
    stream_place_interval_ms: int = 500
    stream_max_inventory_u: float = 1000.0
    stream_pause_buy_inventory_u: float = 800.0
    stream_sell_first: bool = True
    stream_target_ticks: int = 20
    stream_min_profit_ticks: int = 8
    stream_sell_timeout_ms: int = 4000
    stream_sell_retry_max: int = 2
    stream_sell_retry_step_ticks: int = 4
    stream_loss_cooldown_ms: int = 6000
    stream_buy_max_distance_ticks: int = 300
    stream_recenter_interval_ms: int = 3000
    stream_order_error_cooldown_ms: int = 1500
    stream_max_order_errors: int = 5
    # legacy compatibility (hidden in GUI)
    micro_grid_max_active_buys: int = 8
    micro_grid_place_batch_size: int = 3
    micro_grid_place_interval_ms: int = 500
    micro_grid_max_inventory_u: float = 1000.0
    micro_grid_pause_buy_if_inventory_u_above: float = 800.0
    micro_grid_sell_first: bool = True


@dataclass(frozen=True)
class UBConfig:
    display_symbol: str = "BTC/U"
    binance_symbol: str = "BTCU"
    stream_symbol: str = "btcu"
    tick_size_default: float = 0.01


class SettingsStore:
    def __init__(self, path: str = "config/settings.json") -> None:
        self.path = Path(path)
        self.last_merge_missing_added = 0
        self.last_merge_existing_preserved = True

    def load(self) -> SettingsData:
        if not self.path.exists():
            data = SettingsData()
            self.save(data)
            return data
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        current = asdict(SettingsData())
        known_keys = set(current.keys())
        if "lot_size" in payload and "order_size_u" not in payload:
            legacy_lot = float(payload.get("lot_size", 0.0) or 0.0)
            payload["order_size_u"] = legacy_lot if legacy_lot > 1 else 20.0
            payload["legacy_qty_btc"] = legacy_lot if legacy_lot < 1 else 0.0
        if "live_max_exposure_u" in payload and "max_live_exposure_u" not in payload:
            payload["max_live_exposure_u"] = float(payload["live_max_exposure_u"])
        stream_legacy_map = {
            "conveyor_stream_count": "stream_count",
            "conveyor_stream_range_ticks": "stream_range_ticks",
            "conveyor_stream_max_active_buys": "stream_max_active_buys",
            "conveyor_stream_place_batch_size": "stream_place_batch_size",
            "conveyor_stream_place_interval_ms": "stream_place_interval_ms",
            "conveyor_stream_max_inventory_u": "stream_max_inventory_u",
            "conveyor_stream_pause_buy_inventory_u": "stream_pause_buy_inventory_u",
            "conveyor_stream_sell_first": "stream_sell_first",
            "conveyor_stream_target_ticks": "stream_target_ticks",
            "conveyor_stream_min_profit_ticks": "stream_min_profit_ticks",
            "conveyor_stream_sell_timeout_ms": "stream_sell_timeout_ms",
            "conveyor_stream_sell_retry_max": "stream_sell_retry_max",
            "conveyor_stream_sell_retry_step_ticks": "stream_sell_retry_step_ticks",
            "conveyor_stream_loss_cooldown_ms": "stream_loss_cooldown_ms",
            "conveyor_stream_order_error_cooldown_ms": "stream_order_error_cooldown_ms",
            "conveyor_stream_max_order_errors": "stream_max_order_errors",
            "micro_grid_max_active_buys": "stream_max_active_buys",
            "micro_grid_place_batch_size": "stream_place_batch_size",
            "micro_grid_place_interval_ms": "stream_place_interval_ms",
            "micro_grid_max_inventory_u": "stream_max_inventory_u",
            "micro_grid_pause_buy_if_inventory_u_above": "stream_pause_buy_inventory_u",
            "micro_grid_sell_first": "stream_sell_first",
        }
        migrated_stream_keys: list[str] = []
        for old_key, new_key in stream_legacy_map.items():
            if new_key not in payload and old_key in payload:
                payload[new_key] = payload[old_key]
                migrated_stream_keys.append(f"{old_key}->{new_key}")
        ignored_legacy_keys: list[str] = []
        for key, value in payload.items():
            if key in {"max_live_exposure_u", "micro_grid_budget_u", "stream_max_inventory_u", "stream_pause_buy_inventory_u", "micro_grid_max_inventory_u", "micro_grid_pause_buy_if_inventory_u_above"}:
                ignored_legacy_keys.append(key)
                continue
            if key in known_keys:
                current[key] = value

        migrated_tick_keys: list[str] = []
        legacy_map = {
            "min_spread": "min_spread_ticks",
            "entry_offset": "entry_offset_ticks",
            "exit_offset": "exit_offset_ticks",
            "target_capture": "target_capture_ticks",
            "stop_loss": "stop_loss_ticks",
        }
        for legacy_key, tick_key in legacy_map.items():
            if tick_key not in payload and legacy_key in payload:
                current[tick_key] = int(float(payload[legacy_key]))
                migrated_tick_keys.append(tick_key)
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
        missing_added = sum(1 for key in known_keys if key not in payload)
        self.last_merge_missing_added = missing_added
        self.last_merge_existing_preserved = True
        data = SettingsData(**current)
        if migrated_tick_keys:
            print("SETTINGS_MIGRATE_TICKS " + ",".join(sorted(migrated_tick_keys)))
        if migrated_stream_keys:
            print("SETTINGS_MIGRATE_LEGACY_STREAMS " + ",".join(sorted(migrated_stream_keys)))
        for legacy_key in sorted(set(ignored_legacy_keys)):
            print(f"LEGACY_SETTING_IGNORED key={legacy_key}")
        if missing_added > 0:
            self.save(data)
        return data

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
        safe["settings_schema_version"] = "0.8.8-streams-clean"
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
