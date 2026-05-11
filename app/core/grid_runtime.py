from __future__ import annotations

from dataclasses import dataclass, field

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

    def start_dry(self) -> None:
        self.state = "DRY_VIEW"

    def arm_live(self, confirmed: bool) -> None:
        self.user_confirmed = confirmed
        self.state = "LIVE_READY" if confirmed else "DRY_VIEW"

    def start_live(self) -> bool:
        if self.live_enabled and self.user_confirmed:
            self.state = "LIVE_RUNNING"
            return True
        self.state = "ERROR"
        return False

    def pause(self) -> None:
        self.state = "PAUSED"

    def stop(self) -> None:
        self.state = "STOPPING"

    def can_place_level(self, level_id: int, side: str) -> bool:
        return not self.registry.has_active_level_order(level_id, side)
