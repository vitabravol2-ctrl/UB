from dataclasses import dataclass


@dataclass(frozen=True)
class UBConfig:
    display_symbol: str = "BTCU"
    binance_symbol: str = "BTCUUSDT"
    stream_symbol: str = "btcuusdt"
    min_spread: float = 7.0
    entry_offset: float = 1.0
    exit_offset: float = 1.0
    tick_size_default: float = 0.01
    max_ws_age_ms: int = 5000
    rest_poll_ms: int = 2000
    live_enabled: bool = False


CONFIG = UBConfig()
