from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from app.core.grid_order_registry import GridOrderRegistry
from app.core.grid_risk_guard import GridRiskGuard


@dataclass
class GridRuntime:
    state: str = "IDLE"
    live_enabled: bool = False
    user_confirmed: bool = False
    max_exposure_u: float = 0.0
    budget_u: float = 0.0
    registry: GridOrderRegistry = field(default_factory=GridOrderRegistry)
    risk: GridRiskGuard = field(default_factory=GridRiskGuard)
    log_callback: Callable[[str], None] | None = None

    def _log(self, message: str) -> None:
        if self.log_callback:
            self.log_callback(message)

    def start_dry(self) -> tuple[str, str]:
        self.state = "DRY_VIEW"
        return self.state, "OK"

    def arm_live(self, confirmed: bool) -> tuple[str, str]:
        self.user_confirmed = confirmed
        self.state = "LIVE_READY" if confirmed else "DRY_VIEW"
        return self.state, "OK"

    def start_live(self) -> tuple[str, str]:
        if self.live_enabled and self.user_confirmed:
            self.state = "LIVE_RUNNING"
            return self.state, "OK"
        self.state = "ERROR"
        return self.state, "LIVE_NOT_ARMED"

    def pause(self) -> tuple[str, str]:
        self.state = "PAUSED"
        return self.state, "OK"

    def stop(self) -> tuple[str, str]:
        if self.state in {"IDLE", "STOPPED"}:
            self.state = "STOPPED"
            return self.state, "ALREADY_STOPPED"
        self.state = "STOPPED"
        return self.state, "OK"

    def can_place_level(self, level_id: int, side: str) -> bool:
        return not self.registry.has_active_level_order(level_id, side)

    def validate_inputs(self, levels=None, market=None, balances=None, filters=None) -> tuple[str, str]:
        if not levels:
            return self.state, "EMPTY_LEVELS"
        if market is None:
            return self.state, "NO_MARKET"
        if balances is None:
            return self.state, "NO_BALANCES"
        if filters is None:
            return self.state, "NO_FILTERS"
        return self.state, "OK"
