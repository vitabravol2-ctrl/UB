from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RiskResult:
    ok: bool
    reason: str = ""


class GridRiskGuard:
    def validate(self, *, live_enabled: bool, user_confirmed: bool, api_ok: bool, filters_ok: bool, balance_u: float, qty: float, min_qty: float, notional: float, min_notional: float, exposure_u: float, max_exposure_u: float, active_orders_count: int, levels: int, price: float, lower_price: float, upper_price: float, market_stale: bool, duplicate_level_order: bool) -> RiskResult:
        if not live_enabled or not user_confirmed:
            return RiskResult(False, "RISK_LIVE_DISABLED")
        if not api_ok or not filters_ok:
            return RiskResult(False, "RISK_INVALID_LEVEL")
        if market_stale:
            return RiskResult(False, "RISK_MARKET_STALE")
        if balance_u <= 0:
            return RiskResult(False, "RISK_NO_BALANCE")
        if duplicate_level_order:
            return RiskResult(False, "RISK_DUPLICATE_ORDER")
        if qty < min_qty or notional < min_notional:
            return RiskResult(False, "RISK_INVALID_LEVEL")
        if exposure_u > max_exposure_u:
            return RiskResult(False, "RISK_MAX_EXPOSURE")
        if active_orders_count > levels:
            return RiskResult(False, "RISK_INVALID_LEVEL")
        if price < lower_price or price > upper_price:
            return RiskResult(False, "RISK_PRICE_OUT_OF_RANGE")
        return RiskResult(True)
