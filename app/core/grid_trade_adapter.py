from __future__ import annotations

from typing import Any

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

    def place_limit_buy(self, price: float, qty: float) -> dict[str, Any]:
        if not self.live_enabled:
            return {"status": "LIVE_LOCKED", "side": "BUY", "price": price, "qty": qty}
        return self.account.place_limit_order(CONFIG.binance_symbol, "BUY", price, qty)

    def place_limit_sell(self, price: float, qty: float) -> dict[str, Any]:
        if not self.live_enabled:
            return {"status": "LIVE_LOCKED", "side": "SELL", "price": price, "qty": qty}
        return self.account.place_limit_order(CONFIG.binance_symbol, "SELL", price, qty)

    def cancel_order(self, order_id: int) -> dict[str, Any]:
        if not self.live_enabled:
            return {"status": "LIVE_LOCKED", "order_id": order_id}
        return self.account.cancel_order(CONFIG.binance_symbol, order_id)

    def cancel_grid_orders(self) -> dict[str, Any]:
        return {"status": "LOCKED", "message": "Cancel Grid Orders disabled until registry-backed live mode"}
