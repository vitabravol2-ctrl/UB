from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any


@dataclass
class GridOrderRecord:
    order_id: int
    level_id: int
    side: str
    price: float
    qty: float


class GridOrderRegistry:
    def __init__(self) -> None:
        self._orders: dict[int, GridOrderRecord] = {}

    def add(self, order_id: int, level_id: int, side: str, price: float, qty: float) -> None:
        self._orders[order_id] = GridOrderRecord(order_id=order_id, level_id=level_id, side=side, price=price, qty=qty)

    def remove(self, order_id: int) -> None:
        self._orders.pop(order_id, None)

    def find_by_level(self, level_id: int) -> list[dict[str, Any]]:
        return [asdict(rec) for rec in self._orders.values() if rec.level_id == level_id]

    def list_active(self) -> list[dict[str, Any]]:
        return [asdict(rec) for rec in self._orders.values()]

    def clear(self) -> None:
        self._orders.clear()
