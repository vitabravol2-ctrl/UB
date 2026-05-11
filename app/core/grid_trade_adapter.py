from __future__ import annotations

from typing import Any
from uuid import uuid4

from app.core.binance_account import BinanceAccountClient
from app.core.config import CONFIG


class GridTradeAdapter:
    def __init__(self, *, live_enabled: bool = False) -> None:
        self.live_enabled = live_enabled
        self.account = BinanceAccountClient()
        self.api_status = "NOT SET"

    def load_api(self) -> str:
        status = self.account.test_account_connection()
        self.api_status = status.status
        return self.api_status

    def refresh_balances(self) -> dict[str, dict[str, float]]:
        return self.account.get_account_balances()

    def load_filters(self) -> dict[str, float | bool]:
        return self.account.get_exchange_filters(CONFIG.binance_symbol)

    def get_open_orders(self) -> list[dict[str, Any]]:
        return self.account.get_open_orders(CONFIG.binance_symbol)

    def _validate_live(self, client_order_id: str | None = None, symbol: str | None = None) -> dict[str, Any] | None:
        if not self.live_enabled:
            return {"status": "LIVE_LOCKED"}
        if symbol and symbol != CONFIG.binance_symbol:
            return {"status": "ERROR", "reason": "INVALID_SYMBOL"}
        if client_order_id is not None and not client_order_id.startswith("UBGRID_"):
            return {"status": "ERROR", "reason": "INVALID_CLIENT_ORDER_ID"}
        return None

    def generate_client_order_id(self) -> str:
        return f"UBGRID_{uuid4().hex[:20]}"

    def place_limit_buy(self, price: float, qty: float, client_order_id: str, symbol: str | None = None) -> dict[str, Any]:
        blocked = self._validate_live(client_order_id, symbol or CONFIG.binance_symbol)
        if blocked:
            return {**blocked, "side": "BUY", "price": price, "qty": qty}
        return self.account.signed_post("/api/v3/order", {"symbol": CONFIG.binance_symbol, "side": "BUY", "type": "LIMIT", "timeInForce": "GTC", "quantity": str(qty), "price": str(price), "newClientOrderId": client_order_id})

    def place_limit_sell(self, price: float, qty: float, client_order_id: str, symbol: str | None = None) -> dict[str, Any]:
        blocked = self._validate_live(client_order_id, symbol or CONFIG.binance_symbol)
        if blocked:
            return {**blocked, "side": "SELL", "price": price, "qty": qty}
        return self.account.signed_post("/api/v3/order", {"symbol": CONFIG.binance_symbol, "side": "SELL", "type": "LIMIT", "timeInForce": "GTC", "quantity": str(qty), "price": str(price), "newClientOrderId": client_order_id})

    def cancel_order(self, order_id: int) -> dict[str, Any]:
        blocked = self._validate_live()
        if blocked:
            return {**blocked, "order_id": order_id}
        return self.account.cancel_order(CONFIG.binance_symbol, order_id)

    def get_order_status(self, order_id: int) -> dict[str, Any]:
        blocked = self._validate_live()
        if blocked:
            return {**blocked, "order_id": order_id}
        return self.account.get_order(CONFIG.binance_symbol, order_id)

    def cancel_grid_orders(self) -> dict[str, Any]:
        blocked = self._validate_live()
        if blocked:
            return blocked
        cancelled = 0
        for order in self.get_open_orders():
            cid = order.get("clientOrderId", "")
            if isinstance(cid, str) and cid.startswith("UBGRID_"):
                self.account.cancel_order(CONFIG.binance_symbol, int(order["orderId"]))
                cancelled += 1
        return {"status": "OK", "cancelled": cancelled}
