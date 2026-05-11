from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TradePlan:
    bid: float | None = None
    ask: float | None = None
    spread: float | None = None
    entry_price: float | None = None
    exit_price: float | None = None
    capture_per_btc: float | None = None
    lot_size: float = 0.0
    expected_profit_u: float | None = None
    stop_price: float | None = None
    stop_loss_u: float | None = None
    risk_reward: float | None = None
    status: str = "NO_DATA"
    reason: str = "Нет рыночных данных"


class TradeMathEngine:
    def build_plan(self, market_state, settings, filters: dict, balances: dict, api_status: str = "NOT SET") -> TradePlan:
        bid = market_state.snapshot.bid
        ask = market_state.snapshot.ask
        lot_size = float(settings.lot_size)

        if bid is None or ask is None:
            return TradePlan(lot_size=lot_size, status="NO_DATA", reason="Нет рыночных данных")

        spread = ask - bid
        entry_price = bid + float(settings.entry_offset)
        exit_price = ask - float(settings.exit_offset)
        capture_per_btc = exit_price - entry_price
        expected_profit_u = capture_per_btc * lot_size
        stop_price = entry_price - float(settings.stop_loss)
        stop_loss_u = float(settings.stop_loss) * lot_size
        risk_reward = (expected_profit_u / stop_loss_u) if stop_loss_u > 0 else None

        plan = TradePlan(
            bid=bid,
            ask=ask,
            spread=spread,
            entry_price=entry_price,
            exit_price=exit_price,
            capture_per_btc=capture_per_btc,
            lot_size=lot_size,
            expected_profit_u=expected_profit_u,
            stop_price=stop_price,
            stop_loss_u=stop_loss_u,
            risk_reward=risk_reward,
            status="READY",
            reason="План готов",
        )

        self.validate_plan(plan, settings, filters, balances, api_status)
        self.classify_opportunity(plan, settings)
        return plan

    def validate_plan(self, plan: TradePlan, settings, filters: dict, balances: dict, api_status: str) -> None:
        if plan.spread is None:
            plan.status = "NO_DATA"
            plan.reason = "Нет рыночных данных"
            return

        if plan.spread < float(settings.min_spread):
            plan.status = "SPREAD_TOO_SMALL"
            plan.reason = "SPREAD_TOO_SMALL"
            return

        if plan.capture_per_btc is None or plan.capture_per_btc <= 0:
            plan.status = "CAPTURE_TOO_SMALL"
            plan.reason = "CAPTURE_TOO_SMALL"
            return

        if plan.capture_per_btc < float(settings.target_capture):
            plan.status = "CAPTURE_TOO_SMALL"
            plan.reason = "CAPTURE_TOO_SMALL"
            return

        fallback_filters = bool(filters.get("fallback"))
        tick = float(filters.get("tickSize", 0.0))
        step = float(filters.get("stepSize", 0.0))
        min_qty = float(filters.get("minQty", 0.0))
        min_notional = float(filters.get("minNotional", 0.0))

        if not fallback_filters and all(v > 0 for v in [tick, step, min_qty, min_notional]):
            if plan.entry_price is not None and (plan.entry_price / tick) % 1 != 0:
                plan.status = "FILTER_FAIL"
                plan.reason = "Entry price не проходит tickSize"
                return
            if plan.exit_price is not None and (plan.exit_price / tick) % 1 != 0:
                plan.status = "FILTER_FAIL"
                plan.reason = "Exit price не проходит tickSize"
                return
            if (plan.lot_size / step) % 1 != 0:
                plan.status = "FILTER_FAIL"
                plan.reason = "Лот не проходит stepSize"
                return
            if plan.lot_size < min_qty:
                plan.status = "FILTER_FAIL"
                plan.reason = "Лот меньше minQty"
                return
            if (plan.entry_price or 0.0) * plan.lot_size < min_notional:
                plan.status = "FILTER_FAIL"
                plan.reason = "Notional меньше minNotional"
                return
        elif fallback_filters:
            plan.reason = "План готов (Filters fallback)"

        required_u = (plan.entry_price or 0.0) * plan.lot_size
        u_free = float(balances.get("U", {}).get("free", 0.0))
        if api_status == "OK":
            if u_free < required_u:
                plan.status = "BALANCE_LOW"
                plan.reason = "BALANCE_LOW"
                return
        else:
            if plan.status in {"READY", "HOT"}:
                plan.status = "WARNING"
                plan.reason = "BALANCE UNKNOWN"

    def classify_opportunity(self, plan: TradePlan, settings) -> None:
        if plan.status not in {"READY", "HOT", "WARNING"}:
            return
        if plan.capture_per_btc is None:
            return
        if plan.capture_per_btc >= float(settings.target_capture) * 1.5:
            plan.status = "HOT"
            plan.reason = "HOT"
        elif plan.status == "READY":
            plan.reason = "READY"
