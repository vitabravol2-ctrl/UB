from __future__ import annotations

import time
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_DOWN

from app.core.price_ticks import sub_ticks
from typing import Callable

from app.core.grid_order_registry import GridOrderRegistry
from app.core.grid_risk_guard import GridRiskGuard


@dataclass
class GridLevel:
    level_id: int
    target_buy_price: float
    budget_u: float
    qty: float
    state: str = "WAIT_BUY"
    active_buy_order_id: int | None = None
    linked_inventory_chunk_ids: list[str] = field(default_factory=list)
    last_fill_ts: int = 0


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
    levels: list[GridLevel] = field(default_factory=list)

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

    @staticmethod
    def _round_down(value: float, step: float) -> float:
        if step <= 0:
            return value
        dec_value = Decimal(str(value))
        dec_step = Decimal(str(step))
        return float((dec_value / dec_step).to_integral_value(rounding=ROUND_DOWN) * dec_step)

    def configure_micro_grid(self, bid: float, tick_size: float, step_size: float, min_qty: float, min_notional: float, settings) -> list[GridLevel]:
        self.levels = []
        if not settings.micro_grid_enabled:
            self._log("GRID_LEVEL_DISABLED")
            return self.levels
        levels_count = int(settings.micro_grid_size_ticks / settings.micro_grid_step_ticks)
        if levels_count <= 0:
            self._log("GRID_LEVEL_SKIP reason=INVALID_LEVELS")
            return self.levels
        budget_per_level = settings.micro_grid_budget_u / levels_count
        self._log(f"GRID_PARALLEL_START levels={levels_count} budget={settings.micro_grid_budget_u:.2f}")
        self._log(f"GRID_BUDGET_ALLOC levels={levels_count} budget={settings.micro_grid_budget_u:.2f} per_level={budget_per_level:.2f}")
        for idx in range(1, levels_count + 1):
            price = sub_ticks(bid, settings.micro_grid_step_ticks * idx, tick_size)
            qty = self._round_down((budget_per_level / price) if price > 0 else 0.0, step_size)
            notional = qty * price
            if qty < min_qty or notional < min_notional:
                self._log(f"GRID_LEVEL_SKIP_INVALID_QTY level_id={idx} price={price:.8f} qty={qty:.8f}")
                continue
            level = GridLevel(level_id=idx, target_buy_price=price, budget_u=budget_per_level, qty=qty)
            self.levels.append(level)
        self._log(
            f"GRID_LEVELS_CREATED count={len(self.levels)} budget={settings.micro_grid_budget_u:.2f} "
            f"step={settings.micro_grid_step_ticks} size={settings.micro_grid_size_ticks}"
        )
        return self.levels

    def mark_buy_placed(self, level_id: int, order_id: int) -> None:
        level = next((x for x in self.levels if x.level_id == level_id), None)
        if not level:
            return
        level.state = "WAIT_BUY_FILL"
        level.active_buy_order_id = order_id
        self._log(f"GRID_BUY_PLACED level_id={level_id} order_id={order_id}")

    def mark_buy_filled(self, level_id: int) -> None:
        level = next((x for x in self.levels if x.level_id == level_id), None)
        if not level:
            return
        level.state = "BUY_FILLED"
        level.active_buy_order_id = None
        level.last_fill_ts = int(time.time() * 1000)
        self._log(f"GRID_BUY_FILLED level_id={level_id}")

    def recycle_level(self, level_id: int) -> None:
        level = next((x for x in self.levels if x.level_id == level_id), None)
        if not level:
            return
        level.state = "WAIT_BUY"
        level.active_buy_order_id = None
        level.linked_inventory_chunk_ids.clear()
        self._log(f"GRID_LEVEL_RECYCLED level_id={level_id}")

    def grid_telemetry(self, inventory_u: float = 0.0, buy_paused: bool = False, placement_queue: int = 0, last_batch_size: int = 0) -> dict[str, float | int | str]:
        active = len(self.levels)
        open_buys = sum(1 for lvl in self.levels if lvl.state == "WAIT_BUY_FILL" and lvl.active_buy_order_id is not None)
        filled = sum(1 for lvl in self.levels if lvl.state == "BUY_FILLED")
        used = sum(lvl.budget_u for lvl in self.levels if lvl.state == "BUY_FILLED")
        total = sum(lvl.budget_u for lvl in self.levels)
        return {
            "GRID LEVELS": active,
            "GRID ACTIVE BUYS": open_buys,
            "GRID ACTIVE SELLS": filled,
            "GRID INVENTORY U": max(inventory_u, 0.0),
            "GRID BUY PAUSED": "YES" if buy_paused else "NO",
            "GRID PLACEMENT QUEUE": max(placement_queue, 0),
            "GRID LAST BATCH SIZE": max(last_batch_size, 0),
            "GRID FILLED LEVELS": filled,
            "GRID BUDGET USED": used,
            "GRID BUDGET FREE": max(total - used, 0.0),
            "GRID MODE": "PARALLEL" if self.levels else "OFF",
        }

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
