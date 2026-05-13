from __future__ import annotations

import time
from typing import Any

from app.core.binance_account import BinanceAccountClient
from app.core.config import CONFIG


class GridTradeAdapter:
    def __init__(self, *, live_enabled: bool = False, symbol: str | None = None) -> None:
        self.live_enabled = live_enabled
        self.symbol = symbol or CONFIG.binance_symbol
        self.account = BinanceAccountClient()
        self.api_status = "NOT SET"

    def has_keys(self) -> bool:
        return self.account.has_keys()

    def check_api_ready(self) -> tuple[bool, str, str]:
        status = self.account.test_account_connection()
        if status.status == "OK":
            self.api_status = "OK"
            return True, "OK", ""
        if status.status == "NOT SET":
            self.api_status = "NOT SET"
            return False, "NOT SET", "missing_keys"
        self.api_status = "ERROR"
        reason = status.message or "unknown"
        return False, "ERROR", reason

    def load_api(self) -> str:
        _ok, status, _reason = self.check_api_ready()
        return status

    def refresh_balances(self) -> dict[str, dict[str, float]]:
        return self.account.get_account_balances()

    def load_filters(self) -> dict[str, float | bool]:
        return self.account.get_exchange_filters(self.symbol)

    def get_open_orders(self) -> list[dict[str, Any]]:
        return self.account.get_open_orders(self.symbol)

    def get_grid_open_orders(self) -> list[dict[str, Any]]:
        return [o for o in self.get_open_orders() if str(o.get("clientOrderId", "")).startswith("UBGRID_")]

    def _validate_live(self, client_order_id: str | None = None, symbol: str | None = None) -> dict[str, Any] | None:
        if not self.live_enabled:
            return {"status": "LIVE_LOCKED"}
        if symbol and symbol != self.symbol:
            return {"status": "ERROR", "reason": "INVALID_SYMBOL"}
        if client_order_id is not None and not client_order_id.startswith("UBGRID_"):
            return {"status": "ERROR", "reason": "INVALID_CLIENT_ORDER_ID"}
        return None

    def generate_client_order_id(self, side: str = "X", level: int = 0) -> str:
        return f"UBGRID_{side}_{level}_{int(time.time() * 1000)}"

    def place_limit_buy(self, symbol: str, price: float, qty: float, client_order_id: str) -> dict[str, Any]:
        blocked = self._validate_live(client_order_id, symbol)
        if blocked:
            return {**blocked, "side": "BUY", "price": price, "qty": qty}
        return self.account.signed_post("/api/v3/order", {"symbol": symbol, "side": "BUY", "type": "LIMIT", "timeInForce": "GTC", "quantity": str(qty), "price": str(price), "newClientOrderId": client_order_id})

    def place_limit_sell(self, symbol: str, price: float, qty: float, client_order_id: str) -> dict[str, Any]:
        blocked = self._validate_live(client_order_id, symbol)
        if blocked:
            return {**blocked, "side": "SELL", "price": price, "qty": qty}
        return self.account.signed_post("/api/v3/order", {"symbol": symbol, "side": "SELL", "type": "LIMIT", "timeInForce": "GTC", "quantity": str(qty), "price": str(price), "newClientOrderId": client_order_id})

    def cancel_grid_orders(self) -> dict[str, Any]:
        blocked = self._validate_live()
        if blocked:
            return blocked
        cancelled = 0
        for order in self.get_grid_open_orders():
            self.account.cancel_order(self.symbol, int(order["orderId"]))
            cancelled += 1
        return {"status": "OK", "cancelled": cancelled}
