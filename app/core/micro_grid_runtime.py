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
        self.log(f"GRID_RANGE_CONFIG lower={lower_price} upper={upper_price} count={grid_count} step_u={self.step_u} step_ticks={self.step_ticks}")

        for i in range(grid_count):
            buy_price = self._round_down(lower_price + self.step_u * i, tick_size)
            sell_price = self._round_down(buy_price + self.step_u, tick_size)
            if buy_price <= 0 or sell_price <= 0:
                continue
            qty = self._round_down(self.budget_per_level / buy_price, step_size)
            notional = buy_price * qty
            if qty >= min_qty and notional >= min_notional and sell_price * qty >= min_notional:
                self.levels.append(GridLevel(index=i, buy_price=buy_price, sell_price=sell_price, qty=qty))
                self.log(f"GRID_LEVEL_CREATE idx={i} buy={buy_price} sell={sell_price} qty={qty} notional={notional}")

    def select_active_levels(self, current_price: float, max_active_orders: int, free_btc: float) -> tuple[list[GridLevel], list[GridLevel]]:
        below = [lv for lv in self.levels if lv.buy_price < current_price]
        above = [lv for lv in self.levels if lv.sell_price > current_price]
        self.log(f"GRID_ACTIVE_SELECTION current={current_price} buy_candidates={len(below)} sell_candidates={len(above)}")

        buy_slots = min(len(below), max_active_orders)
        self.active_buy_levels = below[-buy_slots:] if buy_slots > 0 else []

        sell_levels: list[GridLevel] = []
        btc_running = 0.0
        for lv in above:
            if btc_running + lv.qty <= free_btc:
                sell_levels.append(lv)
                btc_running += lv.qty
            else:
                self.log(f"GRID_SELL_SKIPPED_NO_BTC required={lv.qty} free={max(0.0, free_btc - btc_running)}")
                break

        remaining_slots = max(0, max_active_orders - len(self.active_buy_levels))
        self.active_sell_levels = sell_levels[:remaining_slots]
        self.log(f"GRID_ACTIVE_PLAN buy_to_place={len(self.active_buy_levels)} sell_to_place={len(self.active_sell_levels)} max_active={max_active_orders}")
        return self.active_buy_levels, self.active_sell_levels

    def place_active_orders(self, *, symbol: str, dry_run: bool, best_bid: float, best_ask: float, test_order_limit: int = 0) -> None:
        if dry_run:
            return
        placed = 0
        if test_order_limit > 0:
            self.log(f"GRID_LIVE_TEST_LIMIT limit={test_order_limit}")

        for lv in self.active_buy_levels:
            if test_order_limit > 0 and placed >= test_order_limit:
                break
            if lv.buy_price >= best_bid:
                continue
            cid = self.adapter.generate_client_order_id(side="B", level=lv.index)
            try:
                resp = self.adapter.place_limit_buy(symbol, lv.buy_price, lv.qty, cid)
                if "orderId" in resp:
                    placed += 1
                    self.log(f"GRID_BUY_PLACED idx={lv.index} price={lv.buy_price} qty={lv.qty} order_id={resp.get('orderId')} client_id={cid}")
                else:
                    self.log(f"GRID_BUY_PLACE_ERROR idx={lv.index} error={resp}")
            except Exception as exc:
                self.log(f"GRID_BUY_PLACE_ERROR idx={lv.index} error={exc}")

        for lv in self.active_sell_levels:
            if test_order_limit > 0 and placed >= test_order_limit:
                break
            if lv.sell_price <= best_ask:
                continue
            cid = self.adapter.generate_client_order_id(side="S", level=lv.index)
            try:
                resp = self.adapter.place_limit_sell(symbol, lv.sell_price, lv.qty, cid)
                if "orderId" in resp:
                    placed += 1
                    self.log(f"GRID_SELL_PLACED idx={lv.index} price={lv.sell_price} qty={lv.qty} order_id={resp.get('orderId')} client_id={cid}")
                else:
                    self.log(f"GRID_SELL_PLACE_ERROR idx={lv.index} error={resp}")
            except Exception as exc:
                self.log(f"GRID_SELL_PLACE_ERROR idx={lv.index} error={exc}")
