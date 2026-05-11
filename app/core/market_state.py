from dataclasses import dataclass, field
import time


@dataclass
class MarketSnapshot:
    bid: float | None = None
    ask: float | None = None
    source: str = "NONE"
    updated_ms: int = field(default_factory=lambda: int(time.time() * 1000))

    @property
    def spread(self) -> float | None:
        if self.bid is None or self.ask is None:
            return None
        return self.ask - self.bid


class MarketState:
    def __init__(self) -> None:
        self.snapshot = MarketSnapshot()
        self.ws_status = "LOST"
        self.rest_status = "N/A"
        self.last_ws_ms: int | None = None
        self.last_rest_ms: int | None = None

    def age_ms(self, timestamp: int | None) -> int | None:
        if timestamp is None:
            return None
        return max(int(time.time() * 1000) - timestamp, 0)
