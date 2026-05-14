from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any
from decimal import Decimal, ROUND_DOWN

from app.core.price_ticks import sub_ticks
from typing import Callable

from app.core.grid_order_registry import GridOrderRegistry
from app.core.grid_risk_guard import GridRiskGuard


@dataclass
class GridLevel:
    level_id: int
    target_buy_price: float
    budget_u: float
    qty: float
    state: str = "WAIT_START"
    active_buy_order_id: int | None = None
    buy_order_id: int | None = None
    active_sell_order_id: int | None = None
    active_chunk_id: int | None = None
    linked_inventory_chunk_ids: list[str] = field(default_factory=list)
    last_fill_ts: int = 0
    start_at_ms: int = 0
    recycle_ready_at_ms: int = 0
    balance_wait_until_ms: int = 0
    paused_error_reason: str = ""
    terminal_result: str = ""
    exit_stuck_attempts: int = 0
    terminal_exit_started: bool = False
    terminal_exit_order_id: int | None = None
    terminal_exit_started_at_ms: int = 0
    terminal_exit_attempts: int = 0


@dataclass
class GridRuntime:
    state: str = "IDLE"
    live_enabled: bool = False
    user_confirmed: bool = False
    max_exposure_u: float = 0.0
    budget_u: float = 0.0
    registry: GridOrderRegistry = field(default_factory=GridOrderRegistry)
    risk: GridRiskGuard = field(default_factory=GridRiskGuard)
    log_callback: Callable[[str], None] | None = None
    levels: list[GridLevel] = field(default_factory=list)
    stream_signal_recent_buy_fill: dict[str, float | int] | None = None
    stream_signal_recent_sell_fill: dict[str, float | int] | None = None
    stream_wait_buy_starvation_since_ms: int = 0
    runtime_started_ms: int = 0

    def _log(self, message: str) -> None:
        if self.log_callback:
            self.log_callback(message)

    def start_dry(self) -> tuple[str, str]:
        self.state = "DRY_VIEW"
        return self.state, "OK"

    def arm_live(self, confirmed: bool) -> tuple[str, str]:
        self.user_confirmed = confirmed
        self.state = "LIVE_READY" if confirmed else "DRY_VIEW"
        return self.state, "OK"

    def start_live(self) -> tuple[str, str]:
        if self.live_enabled and self.user_confirmed:
            self.state = "LIVE_RUNNING"
            return self.state, "OK"
        self.state = "ERROR"
        return self.state, "LIVE_NOT_ARMED"

    def pause(self) -> tuple[str, str]:
        self.state = "PAUSED"
        return self.state, "OK"

    def stop(self) -> tuple[str, str]:
        if self.state in {"IDLE", "STOPPED"}:
            self.state = "STOPPED"
            return self.state, "ALREADY_STOPPED"
        self.state = "STOPPED"
        return self.state, "OK"

    def can_place_level(self, level_id: int, side: str) -> bool:
        return not self.registry.has_active_level_order(level_id, side)

    @staticmethod
    def _round_down(value: float, step: float) -> float:
        if step <= 0:
            return value
        dec_value = Decimal(str(value))
        dec_step = Decimal(str(step))
        return float((dec_value / dec_step).to_integral_value(rounding=ROUND_DOWN) * dec_step)

    def configure_micro_grid(self, bid: float, tick_size: float, step_size: float, min_qty: float, min_notional: float, settings, *, free_u: float | None = None) -> list[GridLevel]:
        self.levels = []
        now_ms = int(time.time() * 1000)
        self.runtime_started_ms = now_ms
        levels_count = max(int(getattr(settings, "stream_count", 1)), 0)
        total_range_ticks = max(int(getattr(settings, "stream_range_ticks", 200)), 0)
        start_interval_ms = max(int(getattr(settings, "stream_start_interval_ms", 0)), 0)
        step_ticks = int(total_range_ticks / levels_count) if levels_count > 0 else 0
        if levels_count <= 0 or step_ticks <= 0:
            self._log("STREAM_SKIP reason=INVALID_STREAMS")
            return self.levels
        if levels_count == 1:
            self._log("STREAM_SINGLE_MODE")
        else:
            self._log(f"STREAM_MULTI_MODE count={levels_count}")
        self._log(f"STREAM_CONVEYOR_ACTIVE count={levels_count} range_ticks={total_range_ticks} step_ticks={step_ticks}")

        order_size_u = max(float(getattr(settings, "order_size_u", 0.0)), 0.0)
        self._log(f"STREAM_BUDGET_SOURCE source=order_size_u order_size_u={order_size_u:.2f}")
        for idx in range(1, levels_count + 1):
            price = sub_ticks(bid, step_ticks * idx, tick_size)
            qty = self._round_down((order_size_u / price) if price > 0 else 0.0, step_size)
            notional = qty * price
            if qty < min_qty or notional < min_notional:
                self._log(f"STREAM_SKIP stream_id={idx} reason=INVALID_QTY price={price:.8f} qty={qty:.8f}")
                continue
            start_at_ms = now_ms + ((idx - 1) * start_interval_ms)
            level = GridLevel(level_id=idx, target_buy_price=price, budget_u=order_size_u, qty=qty, start_at_ms=start_at_ms)
            self.levels.append(level)
            self._log(f"STREAM_CREATE stream_id={idx} buy_price={price:.8f} budget={order_size_u:.2f} qty={qty:.8f}")
            self._log(f"STREAM_WAIT_START stream_id={idx} start_at_ms={start_at_ms}")
        self.activate_ready_streams(now_ms=now_ms)
        self._log(f"STREAM_POOL_CREATED count={levels_count}")
        activated_count = len([lvl for lvl in self.levels if lvl.state != "WAIT_START"])
        self._log(f"STREAM_POOL_ACTIVATED count={activated_count}")
        self._log(f"STREAM_CREATE_DONE count={len(self.levels)} budget_per_level={order_size_u:.2f} step_ticks={step_ticks}")
        return self.levels

    def activate_ready_streams(self, now_ms: int | None = None) -> list[int]:
        ts = int(time.time() * 1000) if now_ms is None else now_ms
        activated: list[int] = []
        for level in self.levels:
            if level.state == "WAIT_START" and ts >= level.start_at_ms:
                level.state = "WAIT_BUY"
                level.recycle_ready_at_ms = 0
                activated.append(level.level_id)
                self._log(f"STREAM_ACTIVATED stream_id={level.level_id} at_ms={ts}")
        return activated

    def validate_stream_contract(self, now_ms: int | None = None) -> tuple[bool, list[str]]:
        ts = int(time.time() * 1000) if now_ms is None else now_ms
        valid_states = {"WAIT_BUY", "BUY_PLACED", "SELL_PLACED", "SELL_RETRY", "SELL_BALANCE_WAIT", "TERMINAL_EXIT", "RECYCLE", "PAUSED_ERROR", "WAIT_START"}
        violations: list[str] = []
        for level in self.levels:
            if int(level.level_id or 0) <= 0:
                violations.append("missing_stream_id")
            if level.state not in valid_states:
                violations.append(f"invalid_state:{level.level_id}:{level.state}")
            if level.state == "BUY_PLACED" and int(level.active_buy_order_id or 0) <= 0:
                violations.append(f"missing_active_buy_order:{level.level_id}")
            if level.state in {"SELL_PLACED", "SELL_RETRY", "TERMINAL_EXIT", "SELL_BALANCE_WAIT"} and int(level.active_chunk_id or 0) <= 0:
                violations.append(f"missing_chunk:{level.level_id}")
            if int(level.active_chunk_id or 0) > 0 and int(level.active_sell_order_id or 0) <= 0:
                has_plan = (int(level.balance_wait_until_ms or 0) > ts) or (level.state in {"SELL_RETRY", "SELL_BALANCE_WAIT"})
                if not has_plan:
                    violations.append(f"chunk_live_without_plan:{level.level_id}")
            if level.state == "PAUSED_ERROR" and not level.paused_error_reason:
                violations.append(f"paused_without_reason:{level.level_id}")
            if level.state == "RECYCLE" and ts >= int(level.recycle_ready_at_ms or 0):
                level.state = "WAIT_BUY"
                level.recycle_ready_at_ms = 0
                self._log(f"STREAM_CONTRACT_REPAIR stream_id={level.level_id} action=recycle_to_wait_buy")
        if violations:
            self._log(f"STREAM_CONTRACT_VIOLATION count={len(violations)} details={'|'.join(violations)}")
            return False, violations
        self._log(f"STREAM_CONTRACT_OK streams={len(self.levels)}")
        return True, []

    def mark_buy_placed(self, level_id: int, order_id: int) -> None:
        level = next((x for x in self.levels if x.level_id == level_id), None)
        if not level:
            return False
        level.state = "BUY_PLACED"
        level.active_buy_order_id = order_id
        level.buy_order_id = order_id
        self._log(f"STREAM_BUY_PLACED level_id={level_id} order_id={order_id}")


    def reset_buy_stream_to_wait(self, level_id: int, reason: str) -> bool:
        level = next((x for x in self.levels if x.level_id == level_id), None)
        if not level:
            return False
        if level.state != "BUY_PLACED":
            return False
        level.active_buy_order_id = None
        level.buy_order_id = None
        level.state = "WAIT_BUY"
        self._log(f"STREAM_BUY_RESET_TO_WAIT stream_id={level_id} reason={reason}")
        return True

    def handle_buy_order_canceled(self, level_id: int, reason: str = "BUY_CANCELED") -> bool:
        reset = self.reset_buy_stream_to_wait(level_id, reason=reason)
        level = next((x for x in self.levels if x.level_id == level_id), None)
        if level and level.state == "BUY_PLACED":
            self._log(f"STREAM_STATE_MISMATCH reason=buy_canceled_still_buy_placed stream_id={level_id}")
        return reset

    def mark_buy_filled(self, level_id: int) -> None:
        level = next((x for x in self.levels if x.level_id == level_id), None)
        if not level:
            return
        level.state = "SELL_PLACED"
        level.active_buy_order_id = None
        level.last_fill_ts = int(time.time() * 1000)
        self._log(f"STREAM_BUY_FILLED level_id={level_id}")
        self.stream_signal_recent_buy_fill = {
            "stream_id": level_id,
            "fill_price": level.target_buy_price,
            "timestamp": level.last_fill_ts,
        }
        self._log(
            f"STREAM_SIGNAL_BUY_FILL stream_id={level_id} fill_price={level.target_buy_price:.8f} effect=log_only"
        )

    def recycle_level(self, level_id: int, recycle_delay_ms: int = 0, *, allow_without_sell_fill: bool = False) -> bool:
        level = next((x for x in self.levels if x.level_id == level_id), None)
        if not level:
            return
        delay_ms = max(int(recycle_delay_ms), 0)
        now_ms = int(time.time() * 1000)
        if not allow_without_sell_fill and level.state not in {"SELL_PLACED", "SELL_RETRY", "TERMINAL_EXIT", "SELL_BALANCE_WAIT"}:
            self._log(f"STREAM_RECYCLE_BLOCKED reason=no_confirmed_sell_fill stream_id={level_id} state={level.state}")
            return False
        level.recycle_ready_at_ms = now_ms + delay_ms
        level.state = "RECYCLE"
        level.active_buy_order_id = None
        level.active_sell_order_id = None
        level.active_chunk_id = None
        level.linked_inventory_chunk_ids.clear()
        self._log(f"STREAM_SIGNAL_SELL_FILL stream_id={level_id} timestamp={now_ms} effect=log_only")
        self.stream_signal_recent_sell_fill = {"stream_id": level_id, "timestamp": now_ms}
        self._log(f"STREAM_RECYCLED level_id={level_id} recycle_delay_ms={delay_ms}")
        return True

    def release_recycle_streams(self, now_ms: int | None = None) -> list[int]:
        ts = int(time.time() * 1000) if now_ms is None else now_ms
        released: list[int] = []
        for level in self.levels:
            if level.state == "RECYCLE" and ts >= level.recycle_ready_at_ms:
                level.state = "WAIT_BUY"
                level.recycle_ready_at_ms = 0
                released.append(level.level_id)
                self._log(f"STREAM_RECYCLE_READY stream_id={level.level_id} at_ms={ts}")
        return released

    def stream_supervisor_tick(self, now_ms: int | None = None) -> dict[str, int]:
        ts = int(time.time() * 1000) if now_ms is None else now_ms
        self._log("STREAM_SUPERVISOR_TICK")
        self.activate_ready_streams(now_ms=ts)
        runtime_started_ms = int(self.runtime_started_ms or 0)
        if runtime_started_ms > 0 and ts - runtime_started_ms >= 1000:
            for level in self.levels:
                if level.state == "WAIT_START":
                    level.state = "WAIT_BUY"
                    level.recycle_ready_at_ms = 0
                    self._log(f"STREAM_WAIT_START_REPAIRED stream_id={level.level_id} at_ms={ts}")
        self.release_recycle_streams(now_ms=ts)
        status = {
            "wait_buy": sum(1 for x in self.levels if x.state == "WAIT_BUY"),
            "buy": sum(1 for x in self.levels if x.state == "BUY_PLACED"),
            "sell": sum(1 for x in self.levels if x.state in {"WAIT_SELL", "SELL_PLACED", "SELL_RETRY"}),
            "exiting": sum(1 for x in self.levels if x.state in {"EXITING", "TERMINAL_EXIT"}),
            "recycle": sum(1 for x in self.levels if x.state in {"RECYCLE", "RECYCLE_COOLDOWN"}),
            "error": sum(1 for x in self.levels if x.state in {"PAUSED_ERROR", "ERROR"}),
        }
        self._log(
            "STREAM_POOL_STATUS "
            f"wait_buy={status['wait_buy']} buy={status['buy']} sell={status['sell']} "
            f"exiting={status['exiting']} recycle={status['recycle']} error={status['error']}"
        )
        return status

    def stream_capacity_fill_plan(self, *, runtime_active: bool, max_active_buys: int, now_ms: int | None = None) -> dict[str, int | bool]:
        status = self.stream_supervisor_tick(now_ms=now_ms)
        active_buys = int(status["buy"])
        wait_buy = int(status["wait_buy"])
        target = max(int(max_active_buys), 0)
        free_slots = max(0, target - active_buys)
        should_fill = bool(runtime_active and wait_buy > 0 and free_slots > 0)
        return {"target": target, "active": active_buys, "wait_buy": wait_buy, "free_slots": free_slots, "should_fill": should_fill}

    def grid_telemetry(self, inventory_u: float = 0.0, buy_paused: bool = False, placement_queue: int = 0, last_batch_size: int = 0) -> dict[str, float | int | str]:
        active = len(self.levels)
        open_buys = sum(1 for lvl in self.levels if lvl.state == "BUY_PLACED" and lvl.active_buy_order_id is not None)
        filled = sum(1 for lvl in self.levels if lvl.state in {"SELL_PLACED", "SELL_RETRY", "TERMINAL_EXIT", "SELL_BALANCE_WAIT"})
        used = sum(lvl.budget_u for lvl in self.levels if lvl.state in {"SELL_PLACED", "SELL_RETRY", "TERMINAL_EXIT", "SELL_BALANCE_WAIT"})
        total = sum(lvl.budget_u for lvl in self.levels)
        return {
            "GRID LEVELS": active,
            "GRID ACTIVE BUYS": open_buys,
            "GRID ACTIVE SELLS": filled,
            "GRID INVENTORY U": max(inventory_u, 0.0),
            "GRID BUY PAUSED": "YES" if buy_paused else "NO",
            "GRID PLACEMENT QUEUE": max(placement_queue, 0),
            "GRID LAST BATCH SIZE": max(last_batch_size, 0),
            "GRID FILLED LEVELS": filled,
            "GRID BUDGET USED": used,
            "GRID BUDGET FREE": max(total - used, 0.0),
            "GRID MODE": "STREAM" if self.levels else "OFF",
        }

    def start_terminal_exit(self, level_id: int, chunk_id: int, order_id: int, now_ms: int | None = None) -> bool:
        level = next((x for x in self.levels if x.level_id == level_id), None)
        if not level:
            return False
        if level.terminal_exit_started:
            self._log(
                f"STREAM_TERMINAL_EXIT_ALREADY_ACTIVE stream_id={level_id} chunk_id={level.active_chunk_id or chunk_id} order_id={level.terminal_exit_order_id}"
            )
            return False
        ts = int(time.time() * 1000) if now_ms is None else int(now_ms)
        level.state = "TERMINAL_EXIT"
        level.active_chunk_id = chunk_id
        level.active_sell_order_id = order_id
        level.terminal_exit_started = True
        level.terminal_exit_order_id = order_id
        level.terminal_exit_started_at_ms = ts
        level.terminal_exit_attempts = 0
        level.terminal_result = ""
        self._log(f"STREAM_TERMINAL_EXIT_STARTED stream_id={level_id} chunk_id={chunk_id} order_id={order_id} started_at_ms={ts}")
        return True

    def terminal_exit_status_wait(self, level_id: int, now_ms: int | None = None) -> bool:
        level = next((x for x in self.levels if x.level_id == level_id), None)
        if not level or not level.terminal_exit_started:
            return False
        ts = int(time.time() * 1000) if now_ms is None else int(now_ms)
        self._log(
            f"STREAM_TERMINAL_EXIT_STATUS_WAIT stream_id={level_id} chunk_id={level.active_chunk_id} order_id={level.terminal_exit_order_id} at_ms={ts}"
        )
        return True

    def finalize_terminal_exit(self, level_id: int, result: str, *, now_ms: int | None = None) -> bool:
        level = next((x for x in self.levels if x.level_id == level_id), None)
        if not level or not level.terminal_exit_started:
            return False
        ts = int(time.time() * 1000) if now_ms is None else int(now_ms)
        normalized = str(result).upper()
        level.terminal_result = normalized
        level.terminal_exit_started = False
        if normalized == "FILLED":
            self._log(f"STREAM_TERMINAL_EXIT_FILLED stream_id={level_id} chunk_id={level.active_chunk_id} order_id={level.terminal_exit_order_id} at_ms={ts}")
        elif normalized in {"FAILED", "PAUSED_ERROR"}:
            self._log(f"STREAM_TERMINAL_EXIT_FAILED stream_id={level_id} chunk_id={level.active_chunk_id} order_id={level.terminal_exit_order_id} result={normalized} at_ms={ts}")
        else:
            self._log(f"STREAM_TERMINAL_EXIT_FAILED stream_id={level_id} chunk_id={level.active_chunk_id} order_id={level.terminal_exit_order_id} result={normalized} at_ms={ts}")
        if normalized == "FILLED":
            level.active_sell_order_id = None
            level.terminal_exit_order_id = None
        return True

    @staticmethod
    def terminal_exit_should_reprice(*, started_at_ms: int, now_ms: int, attempts: int, timeout_ms: int = 5000, max_reprices: int = 1) -> bool:
        return (now_ms - started_at_ms) >= max(int(timeout_ms), 1) and attempts < max(int(max_reprices), 0)

    def validate_inputs(self, levels=None, market=None, balances=None, filters=None) -> tuple[str, str]:
        if not levels:
            return self.state, "EMPTY_LEVELS"
        if market is None:
            return self.state, "NO_MARKET"
        if balances is None:
            return self.state, "NO_BALANCES"
        if filters is None:
            return self.state, "NO_FILTERS"
        return self.state, "OK"

    @staticmethod
    def stream_sell_retry_plan(
        *,
        entry_price: float,
        best_bid: float,
        best_ask: float,
        tick: float,
        min_profit_ticks: int,
        retry_step_ticks: int,
        stop_loss_ticks: int,
        retry_count: int,
        retry_max: int,
        stuck_attempts: int,
        terminal_exit_started: bool = False,
    ) -> dict[str, float | int | str | bool]:
        tick_safe = max(float(tick), 1e-12)
        if terminal_exit_started:
            return {
                "mode": "terminal_poll",
                "price": 0.0,
                "retry_count_next": retry_count,
                "stuck_attempts_next": stuck_attempts,
                "finalized": False,
                "reason": "terminal_exit_active",
            }
        normalized_retry_step = max(int(retry_step_ticks), 1)
        normalized_retry_max = max(int(retry_max), 0)
        if retry_count < normalized_retry_max:
            ladder_step = retry_count + 1
            top_offset_ticks = max(normalized_retry_step * ladder_step, ladder_step)
            floor_target = entry_price + (tick_safe * max(int(min_profit_ticks), 0))
            adaptive_floor = max(floor_target - (tick_safe * max(ladder_step - 1, 0)), tick_safe)
            ask_anchor = (best_ask - (tick_safe * top_offset_ticks)) if best_ask > 0 else floor_target
            target_price = max(adaptive_floor, ask_anchor, tick_safe)
            return {
                "mode": "retry",
                "price": target_price,
                "retry_count_next": retry_count + 1,
                "stuck_attempts_next": stuck_attempts,
                "finalized": False,
                "reason": f"retry_ladder_{ladder_step}",
            }
        stuck_next = stuck_attempts + 1
        attempt_ticks = normalized_retry_step * max(stuck_next, 1)
        bid_anchor = (best_bid - (tick_safe * attempt_ticks)) if best_bid > 0 else (entry_price - tick_safe * attempt_ticks)
        stop_loss_floor = entry_price - (tick_safe * max(int(stop_loss_ticks), 0))
        target_price = max(min(bid_anchor, entry_price), stop_loss_floor, tick_safe)
        finalized = stuck_next >= 3
        return {
            "mode": "stuck_exit",
            "price": target_price,
            "retry_count_next": retry_count,
            "stuck_attempts_next": stuck_next,
            "finalized": finalized,
            "reason": "retry_exhausted_finalized" if finalized else "retry_exhausted_reprice",
        }
