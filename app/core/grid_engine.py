from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN


@dataclass
class GridLevel:
    level: int
    price: float
    side: str
    order_u: float
    qty_btc: float
    status: str
    target_sell: float
    expected_pnl: float
    notional_u: float
    valid: bool
    reason: str


class GridEngine:
    def __init__(
        self,
        tick_size: float = 0.01,
        step_size: float = 0.00001,
        min_qty: float = 0.00001,
        min_notional: float = 5.0,
    ) -> None:
        self.tick_size = tick_size
        self.step_size = step_size
        self.min_qty = min_qty
        self.min_notional = min_notional

    def set_filters(self, tick_size: float, step_size: float, min_qty: float, min_notional: float) -> None:
        self.tick_size = tick_size
        self.step_size = step_size
        self.min_qty = min_qty
        self.min_notional = min_notional

    @staticmethod
    def _round_down(value: float, step: float) -> float:
        dec_value = Decimal(str(value))
        dec_step = Decimal(str(step))
        return float((dec_value / dec_step).to_integral_value(rounding=ROUND_DOWN) * dec_step)

    def calculate_levels(
        self,
        upper_price: float,
        lower_price: float,
        budget_u: float,
        levels: int,
        profit_ticks: int,
    ) -> list[GridLevel]:
        if levels < 2:
            raise ValueError("levels must be >= 2")
        if upper_price <= lower_price:
            raise ValueError("upper_price must be greater than lower_price")

        step = (upper_price - lower_price) / (levels - 1)
        order_u = budget_u / levels
        rows: list[GridLevel] = []

        for index in range(levels):
            raw_price = upper_price - step * index
            price = self._round_down(raw_price, self.tick_size)
            raw_qty_btc = order_u / price
            qty_btc = self._round_down(raw_qty_btc, self.step_size)
            notional_u = price * qty_btc
            target_sell = price + profit_ticks * self.tick_size
            expected_pnl = (target_sell - price) * qty_btc

            valid = True
            reason = "OK"
            status = "PLANNED"
            if qty_btc < self.min_qty:
                valid = False
                reason = "INVALID_MIN_QTY"
                status = reason
            elif notional_u < self.min_notional:
                valid = False
                reason = "INVALID_MIN_NOTIONAL"
                status = reason

            rows.append(
                GridLevel(
                    level=index + 1,
                    price=price,
                    side="BUY",
                    order_u=order_u,
                    qty_btc=qty_btc,
                    status=status,
                    target_sell=target_sell,
                    expected_pnl=expected_pnl,
                    notional_u=notional_u,
                    valid=valid,
                    reason=reason,
                )
            )
        return rows
