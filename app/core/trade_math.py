from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN

from app.core.price_ticks import add_ticks, spread_ticks, sub_ticks, ticks_to_price


@dataclass
class TradePlan:
    bid: float | None = None
    ask: float | None = None
    spread: float | None = None
    entry_price: float | None = None
    exit_price: float | None = None
    capture_per_btc: float | None = None
    qty_btc: float = 0.0
    order_size_u: float = 0.0
    expected_profit_u: float | None = None
    stop_price: float | None = None
    stop_loss_u: float | None = None
    risk_reward: float | None = None
    required_u: float | None = None
    balance_ok: bool = False
    filters_ok: bool = False
    status: str = "NO_DATA"
    reason: str = "Нет рыночных данных"


class TradeMathEngine:
    @staticmethod
    def _d(value: float | int | str | None) -> Decimal:
        return Decimal(str(value or 0))

    @staticmethod
    def _round_down_to_step(value: Decimal, step: Decimal) -> Decimal:
        if step <= 0:
            return value
        units = (value / step).to_integral_value(rounding=ROUND_DOWN)
        return units * step

    def build_plan(self, market_state, settings, filters: dict, balances: dict, api_status: str = "NOT SET") -> TradePlan:
        bid = market_state.snapshot.bid
        ask = market_state.snapshot.ask
        order_size_u = self._d(settings.order_size_u)

        if bid is None or ask is None:
            return TradePlan(order_size_u=float(order_size_u), status="NO_DATA", reason="Нет рыночных данных")

        d_bid = self._d(bid)
        d_ask = self._d(ask)
        tick_size = float(filters.get("tickSize", 0.0) or 0.0)
        spread = self._d(ticks_to_price(spread_ticks(float(d_bid), float(d_ask), tick_size), tick_size))
        entry_price = self._d(add_ticks(float(d_bid), int(settings.entry_offset_ticks), tick_size))
        exit_price = self._d(sub_ticks(float(d_ask), int(settings.exit_offset_ticks), tick_size))
        tick = self._d(tick_size)
        step = self._d(filters.get("stepSize", 0.0))
        entry_price = self._round_down_to_step(entry_price, tick)
        exit_price = self._round_down_to_step(exit_price, tick)
        qty_btc = self._round_down_to_step(order_size_u / entry_price if entry_price > 0 else Decimal("0"), step)
        capture_per_btc = exit_price - entry_price
        expected_profit_u = capture_per_btc * qty_btc
        stop_price = self._d(sub_ticks(float(entry_price), int(settings.stop_loss_ticks), tick_size))
        stop_loss_u = self._d(ticks_to_price(int(settings.stop_loss_ticks), tick_size)) * qty_btc
        risk_reward = (expected_profit_u / stop_loss_u) if stop_loss_u > 0 else None
        required_u = entry_price * qty_btc

        plan = TradePlan(
            bid=float(d_bid),
            ask=float(d_ask),
            spread=float(spread),
            entry_price=float(entry_price),
            exit_price=float(exit_price),
            capture_per_btc=float(capture_per_btc),
            qty_btc=float(qty_btc),
            order_size_u=float(order_size_u),
            expected_profit_u=float(expected_profit_u),
            stop_price=float(stop_price),
            stop_loss_u=float(stop_loss_u),
            risk_reward=float(risk_reward) if risk_reward is not None else None,
            required_u=float(required_u),
            status="READY",
            reason="План готов",
        )

        self.validate_plan(plan, settings, filters, balances, api_status)
        self.classify_opportunity(plan, settings)
        return plan

    @staticmethod
    def _target_capture_ticks(settings) -> int:
        raw = getattr(settings, "target_capture_ticks", None)
        if raw is None:
            raw = getattr(settings, "stream_target_ticks", 0)
        try:
            return max(int(raw), 0)
        except (TypeError, ValueError):
            return 0

    def validate_plan(self, plan: TradePlan, settings, filters: dict, balances: dict, api_status: str) -> None:
        if plan.spread is None:
            plan.status = "NO_DATA"
            plan.reason = "Нет рыночных данных"
            return

        if plan.spread < float(ticks_to_price(int(settings.min_spread_ticks), float(filters.get("tickSize", 0.0) or 0.0))):
            plan.status = "SPREAD_TOO_SMALL"
            plan.reason = "SPREAD_TOO_SMALL"
            return

        if plan.capture_per_btc is None or plan.capture_per_btc <= 0:
            plan.status = "CAPTURE_TOO_SMALL"
            plan.reason = "CAPTURE_TOO_SMALL"
            return

        target_capture_ticks = self._target_capture_ticks(settings)
        if plan.capture_per_btc < float(ticks_to_price(target_capture_ticks, float(filters.get("tickSize", 0.0) or 0.0))):
            plan.status = "CAPTURE_TOO_SMALL"
            plan.reason = "CAPTURE_TOO_SMALL"
            return

        fallback_filters = bool(filters.get("fallback"))
        tick = self._d(filters.get("tickSize", 0.0))
        step = self._d(filters.get("stepSize", 0.0))
        min_qty = self._d(filters.get("minQty", 0.0))
        min_notional = self._d(filters.get("minNotional", 0.0))
        entry_price = self._d(plan.entry_price)
        exit_price = self._d(plan.exit_price)
        qty_btc = self._d(plan.qty_btc)

        if not fallback_filters and all(v > 0 for v in [tick, step, min_qty, min_notional]):
            if plan.entry_price is not None and (entry_price % tick) != 0:
                plan.status = "FILTER_FAIL"
                plan.reason = "Entry price не проходит tickSize"
                return
            if plan.exit_price is not None and (exit_price % tick) != 0:
                plan.status = "FILTER_FAIL"
                plan.reason = "Exit price не проходит tickSize"
                return
            if (qty_btc % step) != 0:
                plan.status = "FILTER_FAIL"
                plan.reason = "Qty не проходит stepSize"
                return
            if qty_btc < min_qty:
                plan.status = "FILTER_FAIL"
                plan.reason = "Qty меньше minQty"
                return
            if entry_price * qty_btc < min_notional:
                plan.status = "FILTER_FAIL"
                plan.reason = "Notional меньше minNotional"
                return
            plan.filters_ok = True
        elif fallback_filters:
            plan.reason = "План готов (Filters fallback)"
            plan.filters_ok = True

        required_u = entry_price * qty_btc
        plan.required_u = float(required_u)
        u_free = self._d(balances.get("U", {}).get("free", 0.0))
        if api_status == "OK":
            if u_free < required_u:
                plan.status = "BALANCE_LOW"
                plan.reason = "BALANCE_LOW"
                plan.balance_ok = False
                return
            plan.balance_ok = True
        else:
            if plan.status in {"READY", "HOT"}:
                plan.status = "WARNING"
                plan.reason = "BALANCE UNKNOWN"

    def classify_opportunity(self, plan: TradePlan, settings) -> None:
        if plan.status not in {"READY", "HOT", "WARNING"}:
            return
        if plan.capture_per_btc is None:
            return
        target_capture_ticks = self._target_capture_ticks(settings)
        if plan.capture_per_btc >= float(target_capture_ticks) * 1.5:
            plan.status = "HOT"
            plan.reason = "HOT"
        elif plan.status == "READY":
            plan.reason = "READY"
