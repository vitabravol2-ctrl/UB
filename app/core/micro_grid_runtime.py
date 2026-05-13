from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
from typing import Callable


@dataclass
class GridLevel:
    index: int
    buy_price: float
    sell_price: float
    qty: float


class MicroGridRuntime:
    def __init__(self, adapter, log: Callable[[str], None]) -> None:
        self.adapter = adapter
        self.log = log
        self.levels: list[GridLevel] = []
        self.active_buy_levels: list[GridLevel] = []
        self.active_sell_levels: list[GridLevel] = []
        self.step_u = 0.0
        self.step_ticks = 0
        self.budget_per_level = 0.0

    @staticmethod
    def _round_down(value: float, step: float) -> float:
        return float((Decimal(str(value)) / Decimal(str(step))).to_integral_value(rounding=ROUND_DOWN) * Decimal(str(step))) if step > 0 else value

    def build_range_levels(self, *, lower_price: float, upper_price: float, grid_count: int, total_investment_u: float, tick_size: float, step_size: float, min_qty: float, min_notional: float) -> None:
        self.levels = []
        if lower_price <= 0 or upper_price <= lower_price or grid_count <= 0:
            return

        raw_step = (upper_price - lower_price) / grid_count
        self.step_ticks = max(1, round(raw_step / tick_size)) if tick_size > 0 else 1
        self.step_u = self.step_ticks * tick_size if tick_size > 0 else raw_step
        self.budget_per_level = total_investment_u / grid_count
        self.log(f"GRID_RANGE_CONFIG lower={lower_price} upper={upper_price} count={grid_count} step={self.step_u}")

        for i in range(grid_count):
            buy_price = self._round_down(lower_price + self.step_u * i, tick_size)
            sell_price = self._round_down(buy_price + self.step_u, tick_size)
            qty = self._round_down(self.budget_per_level / buy_price, step_size) if buy_price > 0 else 0.0
            if qty >= min_qty and buy_price * qty >= min_notional and sell_price * qty >= min_notional:
                self.levels.append(GridLevel(index=i, buy_price=buy_price, sell_price=sell_price, qty=qty))
                self.log(f"GRID_LEVEL_CREATE idx={i} buy={buy_price} sell={sell_price} qty={qty}")

    def select_active_levels(self, current_price: float, max_active_orders: int, free_btc: float) -> tuple[list[GridLevel], list[GridLevel]]:
        below = [lv for lv in self.levels if lv.buy_price < current_price]
        above = [lv for lv in self.levels if lv.buy_price > current_price]

        btc_running = 0.0
        sell_candidates: list[GridLevel] = []
        for lv in above:
            if btc_running + lv.qty <= free_btc:
                sell_candidates.append(lv)
                btc_running += lv.qty

        buy_slots = min(len(below), max_active_orders)
        sell_slots = min(len(sell_candidates), max(0, max_active_orders - buy_slots))
        self.active_buy_levels = below[-buy_slots:] if buy_slots > 0 else []
        self.active_sell_levels = sell_candidates[:sell_slots]
        self.log(f"GRID_ACTIVE_LEVELS buy={len(self.active_buy_levels)} sell={len(self.active_sell_levels)}")
        return self.active_buy_levels, self.active_sell_levels

    def place_active_orders(self, *, live_enabled: bool, dry_run: bool, best_bid: float, best_ask: float) -> None:
        if dry_run:
            return
        for lv in self.active_buy_levels:
            if lv.buy_price >= best_bid:
                continue
            resp = self.adapter.place_limit_buy(lv.buy_price, lv.qty, self.adapter.generate_client_order_id(side="B", level=lv.index))
            if "orderId" in resp:
                self.log(f"GRID_BUY_PLACED idx={lv.index}")
        for lv in self.active_sell_levels:
            if lv.sell_price <= best_ask:
                continue
            resp = self.adapter.place_limit_sell(lv.sell_price, lv.qty, self.adapter.generate_client_order_id(side="S", level=lv.index))
            if "orderId" in resp:
                self.log(f"GRID_SELL_PLACED idx={lv.index}")
