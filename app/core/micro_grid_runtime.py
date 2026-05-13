from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from typing import Callable

from app.core.grid_trade_adapter import GridTradeAdapter
from app.core.price_ticks import round_price_to_tick


@dataclass
class GridLevel:
    index: int
    buy_price: float
    sell_price: float
    qty: float
    state: str = "WAIT_BUY"
    buy_order_id: int = 0
    sell_order_id: int = 0


class MicroGridRuntime:
    def __init__(self, adapter: GridTradeAdapter, log: Callable[[str], None]) -> None:
        self.adapter = adapter
        self.log = log
        self.levels: list[GridLevel] = []
        self.step = 0.0
        self.realized_pnl = 0.0
        self.closed_cycles = 0
        self.step_ticks = 0

    @staticmethod
    def _round_up(value: float, step: float) -> float:
        if step <= 0:
            return value
        return float((Decimal(str(value)) / Decimal(str(step))).to_integral_value(rounding=ROUND_UP) * Decimal(str(step)))

    @staticmethod
    def _round_down(value: float, step: float) -> float:
        if step <= 0:
            return value
        return float((Decimal(str(value)) / Decimal(str(step))).to_integral_value(rounding=ROUND_DOWN) * Decimal(str(step)))

    def calculate(self, lower: float, upper: float, grid_count: int, investment_u: float, step_size: float, tick_size: float) -> tuple[float, int, float, float]:
        rng = upper - lower
        self.step = rng / grid_count
        self.step_ticks = max(1, int(round(self.step / tick_size))) if tick_size > 0 else 0
        self.step = self.step_ticks * tick_size if tick_size > 0 else self.step
        budget = investment_u / grid_count
        mid = (lower + upper) / 2
        qty = self._round_down(budget / mid, step_size)
        return self.step, self.step_ticks, budget, qty

    def build_levels(self, lower: float, grid_count: int, investment_u: float, tick_size: float, step_size: float, min_qty: float, min_notional: float) -> None:
        self.levels = []
        budget_per_level = investment_u / grid_count
        for i in range(grid_count):
            raw_buy = lower + self.step * i
            buy = round_price_to_tick(raw_buy, tick_size)
            sell = self._round_up(buy + self.step, tick_size)
            qty = self._round_down(budget_per_level / buy, step_size) if buy > 0 else 0.0
            if qty < min_qty:
                self.log(f"GRID_LEVEL_SKIP idx={i} reason=MIN_QTY")
                continue
            if (buy * qty) < min_notional or (sell * qty) < min_notional:
                self.log(f"GRID_LEVEL_SKIP idx={i} reason=MIN_NOTIONAL")
                continue
            self.levels.append(GridLevel(index=i, buy_price=buy, sell_price=sell, qty=qty))
            self.log(f"GRID_LEVEL_CREATE idx={i} buy={buy:.8f} sell={sell:.8f} qty={qty:.8f}")

    def start(self, max_active_orders: int, live_enabled: bool, dry_run: bool, test_order_limit: int = 3) -> None:
        if dry_run:
            self.log("GRID_DRY_RUN no_orders_placed=1")
            return
        active = 0
        placement_limit = max_active_orders
        if live_enabled:
            placement_limit = min(max_active_orders, test_order_limit)
            self.log(f"GRID_LIVE_TEST_LIMIT limit={placement_limit}")
        for level in self.levels:
            if active >= placement_limit:
                break
            if level.state == "WAIT_BUY":
                cid = self.adapter.generate_client_order_id()
                resp = self.adapter.place_limit_buy(level.buy_price, level.qty, cid)
                if "orderId" in resp:
                    level.buy_order_id = int(resp["orderId"])
                    level.state = "BUY_PLACED"
                    active += 1
                    self.log(f"GRID_BUY_PLACED idx={level.index} order_id={level.buy_order_id} price={level.buy_price} qty={level.qty}")
                else:
                    self.log(f"GRID_BUY_PLACE_ERROR idx={level.index} error={resp}")

    def poll(self) -> None:
        for level in self.levels:
            if level.state == "BUY_PLACED" and level.buy_order_id:
                st = self.adapter.get_order_status(level.buy_order_id)
                if st.get("status") == "FILLED":
                    level.state = "BUY_FILLED"
                    self.log(f"GRID_BUY_FILLED idx={level.index} order_id={level.buy_order_id}")
                    cid = self.adapter.generate_client_order_id()
                    resp = self.adapter.place_limit_sell(level.sell_price, level.qty, cid)
                    if "orderId" in resp:
                        level.sell_order_id = int(resp["orderId"])
                        level.state = "SELL_PLACED"
                        self.log(f"GRID_SELL_PLACED idx={level.index} order_id={level.sell_order_id} price={level.sell_price} qty={level.qty}")
            elif level.state == "SELL_PLACED" and level.sell_order_id:
                st = self.adapter.get_order_status(level.sell_order_id)
                if st.get("status") == "FILLED":
                    level.state = "SELL_FILLED"
                    pnl = (level.sell_price - level.buy_price) * level.qty
                    self.realized_pnl += pnl
                    self.closed_cycles += 1
                    self.log(f"GRID_SELL_FILLED idx={level.index} order_id={level.sell_order_id}")
                    self.log(f"GRID_PNL idx={level.index} pnl={pnl:.8f} total={self.realized_pnl:.8f}")
                    level.buy_order_id = 0
                    level.sell_order_id = 0
                    level.state = "RECYCLED"
                    self.log(f"GRID_RECYCLED idx={level.index}")
                    level.state = "WAIT_BUY"
