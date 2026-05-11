from dataclasses import dataclass


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


class GridEngine:
    def __init__(self, tick_size: float = 0.01) -> None:
        self.tick_size = tick_size

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
            price = upper_price - step * index
            qty_btc = order_u / price
            target_sell = price + profit_ticks * self.tick_size
            expected_pnl = (target_sell - price) * qty_btc
            rows.append(
                GridLevel(
                    level=index + 1,
                    price=price,
                    side="BUY",
                    order_u=order_u,
                    qty_btc=qty_btc,
                    status="PLANNED",
                    target_sell=target_sell,
                    expected_pnl=expected_pnl,
                )
            )
        return rows
