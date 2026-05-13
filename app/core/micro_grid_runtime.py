from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from typing import Callable


@dataclass
class GridLevel:
    index: int
    side: str
    entry_price: float
    exit_price: float
    qty: float
    state: str = "WAIT_ENTRY"
    entry_order_id: int = 0
    exit_order_id: int = 0


class MicroGridRuntime:
    def __init__(self, adapter, log: Callable[[str], None]) -> None:
        self.adapter = adapter
        self.log = log
        self.buy_levels: list[GridLevel] = []
        self.sell_levels: list[GridLevel] = []
        self.levels: list[GridLevel] = []
        self.step = 0.0
        self.realized_pnl = 0.0
        self.closed_cycles = 0
        self.test_placed_buy = 0
        self.test_placed_sell = 0
        self.available_sell_levels = 0

    @staticmethod
    def _round_up(value: float, step: float) -> float:
        return float((Decimal(str(value)) / Decimal(str(step))).to_integral_value(rounding=ROUND_UP) * Decimal(str(step))) if step > 0 else value

    @staticmethod
    def _round_down(value: float, step: float) -> float:
        return float((Decimal(str(value)) / Decimal(str(step))).to_integral_value(rounding=ROUND_DOWN) * Decimal(str(step))) if step > 0 else value

    def build_two_sided_levels(self, mid: float, grid_step_u: float, buy_levels_down: int, sell_levels_up: int, budget_buy_side_u: float, budget_sell_side_u: float, tick_size: float, step_size: float, min_qty: float, min_notional: float) -> None:
        self.step = grid_step_u
        self.buy_levels, self.sell_levels = [], []
        buy_budget = budget_buy_side_u / buy_levels_down
        sell_budget = budget_sell_side_u / sell_levels_up
        self.log(f"GRID_BUDGET_SPLIT buy_u={budget_buy_side_u} sell_u={budget_sell_side_u}")
        for i in range(1, buy_levels_down + 1):
            buy = self._round_down(mid - grid_step_u * i, tick_size)
            sell = self._round_up(buy + grid_step_u, tick_size)
            qty = self._round_down(buy_budget / buy, step_size) if buy > 0 else 0.0
            if qty >= min_qty and buy * qty >= min_notional and sell * qty >= min_notional:
                self.buy_levels.append(GridLevel(index=i, side="BUY", entry_price=buy, exit_price=sell, qty=qty))
                self.log(f"GRID_BUY_LEVEL_CREATE idx={i} buy={buy} sell={sell} qty={qty}")
        for i in range(1, sell_levels_up + 1):
            sell = self._round_up(mid + grid_step_u * i, tick_size)
            buy = self._round_down(sell - grid_step_u, tick_size)
            qty = self._round_down(sell_budget / sell, step_size) if sell > 0 else 0.0
            if qty >= min_qty and buy * qty >= min_notional and sell * qty >= min_notional:
                self.sell_levels.append(GridLevel(index=i, side="SELL", entry_price=sell, exit_price=buy, qty=qty))
                self.log(f"GRID_SELL_LEVEL_CREATE idx={i} sell={sell} buy={buy} qty={qty}")
        self.levels = self.buy_levels + self.sell_levels

    def required_sell_btc(self) -> float:
        return sum(lv.qty for lv in self.sell_levels)

    def check_balances(self, free_u: float, free_btc: float) -> None:
        need_u = sum(lv.entry_price * lv.qty for lv in self.buy_levels)
        need_btc = self.required_sell_btc()
        self.log(f"GRID_BALANCE_CHECK buy_required_u={need_u} sell_required_btc={need_btc} free_u={free_u} free_btc={free_btc}")
        if free_btc < need_btc:
            self.log(f"GRID_BALANCE_INSUFFICIENT side=SELL required_btc={need_btc} free_btc={free_btc}")
            running = 0.0
            allowed = 0
            for lv in self.sell_levels:
                if running + lv.qty <= free_btc:
                    running += lv.qty
                    allowed += 1
            self.available_sell_levels = allowed
        else:
            self.available_sell_levels = len(self.sell_levels)

    def _cid(self, side: str, level: int) -> str:
        return self.adapter.generate_client_order_id(side=side, level=level)

    def start(self, max_live_buy_orders: int, max_live_sell_orders: int, live_enabled: bool, dry_run: bool, test_order_limit: int = 0) -> None:
        if dry_run:
            return
        self.test_placed_buy = 0
        self.test_placed_sell = 0
        buy_allowed = max_live_buy_orders
        sell_allowed = min(max_live_sell_orders, self.available_sell_levels or len(self.sell_levels))
        for lv in self.buy_levels:
            if buy_allowed <= 0:
                break
            if live_enabled and test_order_limit > 0 and self.test_placed_buy >= test_order_limit:
                break
            resp = self.adapter.place_limit_buy(lv.entry_price, lv.qty, self._cid("B", lv.index))
            if "orderId" in resp:
                lv.entry_order_id = int(resp["orderId"])
                lv.state = "ENTRY_PLACED"
                buy_allowed -= 1
                self.test_placed_buy += 1
                self.log(f"GRID_BUY_PLACED idx={lv.index}")
        for lv in self.sell_levels[:sell_allowed]:
            if sell_allowed <= 0:
                break
            if live_enabled and test_order_limit > 0 and self.test_placed_sell >= test_order_limit:
                break
            resp = self.adapter.place_limit_sell(lv.entry_price, lv.qty, self._cid("S", lv.index))
            if "orderId" in resp:
                lv.entry_order_id = int(resp["orderId"])
                lv.state = "ENTRY_PLACED"
                sell_allowed -= 1
                self.test_placed_sell += 1
                self.log(f"GRID_SELL_PLACED idx={lv.index}")

    def poll(self) -> None:
        for lv in self.levels:
            if lv.state == "ENTRY_PLACED" and lv.entry_order_id:
                if self.adapter.get_order_status(lv.entry_order_id).get("status") == "FILLED":
                    if lv.side == "BUY":
                        self.log(f"GRID_BUY_FILLED idx={lv.index}")
                        resp = self.adapter.place_limit_sell(lv.exit_price, lv.qty, self._cid("S", lv.index))
                    else:
                        self.log(f"GRID_SELL_FILLED idx={lv.index}")
                        resp = self.adapter.place_limit_buy(lv.exit_price, lv.qty, self._cid("B", lv.index))
                    if "orderId" in resp:
                        lv.exit_order_id = int(resp["orderId"])
                        lv.state = "EXIT_PLACED"
            elif lv.state == "EXIT_PLACED" and lv.exit_order_id:
                if self.adapter.get_order_status(lv.exit_order_id).get("status") == "FILLED":
                    pnl = abs(lv.entry_price - lv.exit_price) * lv.qty
                    self.realized_pnl += pnl
                    self.closed_cycles += 1
                    self.log(f"GRID_PNL idx={lv.index} pnl={pnl}")
                    lv.entry_order_id = 0
                    lv.exit_order_id = 0
                    lv.state = "WAIT_ENTRY"
                    self.log(f"GRID_RECYCLED idx={lv.index}")
