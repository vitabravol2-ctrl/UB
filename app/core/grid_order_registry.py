from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any


ACTIVE_STATUSES = {"NEW", "PARTIALLY_FILLED", "BUY_PLACED", "SELL_PLACED"}


@dataclass
class GridOrderRecord:
    order_id: int
    client_order_id: str
    level_id: int
    side: str
    price: float
    qty: float
    status: str
    created_at: str
    updated_at: str


class GridOrderRegistry:
    def __init__(self) -> None:
        self._orders: dict[int, GridOrderRecord] = {}

    def add(self, order_id: int, level_id: int, side: str, price: float, qty: float, client_order_id: str = "") -> None:
        now = datetime.utcnow().isoformat()
        self._orders[order_id] = GridOrderRecord(order_id, client_order_id, level_id, side, price, qty, f"{side}_PLACED", now, now)

    def has_active_level_order(self, level_id: int, side: str) -> bool:
        return any(o.level_id == level_id and o.side == side and o.status in ACTIVE_STATUSES for o in self._orders.values())

    def find_by_client_order_id(self, client_order_id: str) -> dict[str, Any] | None:
        for rec in self._orders.values():
            if rec.client_order_id == client_order_id:
                return asdict(rec)
        return None

    def mark_filled(self, order_id: int) -> None:
        self._mark(order_id, "FILLED")

    def mark_cancelled(self, order_id: int) -> None:
        self._mark(order_id, "CANCELLED")

    def mark_error(self, order_id: int) -> None:
        self._mark(order_id, "ERROR")

    def _mark(self, order_id: int, status: str) -> None:
        rec = self._orders.get(order_id)
        if rec:
            rec.status = status
            rec.updated_at = datetime.utcnow().isoformat()

    def remove(self, order_id: int) -> None:
        self._orders.pop(order_id, None)

    def list_active(self) -> list[dict[str, Any]]:
        return [asdict(rec) for rec in self._orders.values() if rec.status in ACTIVE_STATUSES]

    def list_by_level(self, level_id: int) -> list[dict[str, Any]]:
        return [asdict(rec) for rec in self._orders.values() if rec.level_id == level_id]

    def find_by_level(self, level_id: int) -> list[dict[str, Any]]:
        return self.list_by_level(level_id)

    def clear(self) -> None:
        self._orders.clear()
