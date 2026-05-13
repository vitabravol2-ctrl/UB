from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
from typing import Callable

from app.core.grid_trade_adapter import GridTradeAdapter


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

    @staticmethod
    def _round_down(value: float, step: float) -> float:
        if step <= 0:
            return value
        return float((Decimal(str(value)) / Decimal(str(step))).to_integral_value(rounding=ROUND_DOWN) * Decimal(str(step)))

    def calculate(self, lower: float, upper: float, grid_count: int, investment_u: float, step_size: float) -> tuple[float, float, float]:
        rng = upper - lower
        self.step = rng / grid_count
        budget = investment_u / grid_count
        mid = (lower + upper) / 2
        qty = self._round_down(budget / mid, step_size)
        return self.step, budget, qty

    def build_levels(self, lower: float, grid_count: int, qty: float) -> None:
        self.levels = []
        for i in range(grid_count):
            buy = lower + self.step * i
            sell = buy + self.step
            self.levels.append(GridLevel(index=i, buy_price=buy, sell_price=sell, qty=qty))
            self.log(f"GRID_LEVEL_CREATE idx={i} buy={buy:.8f} sell={sell:.8f} qty={qty:.8f}")

    def start(self, max_active_orders: int) -> None:
        active = 0
        for level in self.levels:
            if active >= max_active_orders:
                break
            if level.state == "WAIT_BUY":
                cid = self.adapter.generate_client_order_id()
                resp = self.adapter.place_limit_buy(level.buy_price, level.qty, cid)
                if "orderId" in resp:
                    level.buy_order_id = int(resp["orderId"])
                    level.state = "BUY_PLACED"
                    active += 1
                    self.log(f"GRID_BUY_PLACED idx={level.index} order_id={level.buy_order_id} price={level.buy_price} qty={level.qty}")

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
