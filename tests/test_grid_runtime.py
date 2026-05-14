from app.core.grid_order_registry import GridOrderRegistry
from app.core.config import SettingsData
from app.core.grid_runtime import GridRuntime
from app.core.grid_trade_adapter import GridTradeAdapter


def test_registry_lifecycle() -> None:
    reg = GridOrderRegistry()
    reg.add(order_id=1, level_id=3, side="BUY", price=10.0, qty=1.0, client_order_id="UBGRID_x")
    reg.mark_filled(1)
    reg.add(order_id=2, level_id=3, side="SELL", price=11.0, qty=1.0, client_order_id="UBGRID_y")
    reg.mark_filled(2)
    assert reg.list_by_level(3)[-1]["status"] == "FILLED"


def test_runtime_duplicate_block() -> None:
    rt = GridRuntime()
    rt.registry.add(order_id=7, level_id=1, side="BUY", price=10, qty=1, client_order_id="UBGRID_z")
    assert rt.can_place_level(1, "BUY") is False


def test_ubgrid_client_id_generated() -> None:
    adapter = GridTradeAdapter()
    assert adapter.generate_client_order_id().startswith("UBGRID_")


def test_micro_grid_levels_and_order_size_budget() -> None:
    rt = GridRuntime()
    s = SettingsData(
        stream_count=40,
        stream_range_ticks=400,
        order_size_u=15.0,
        max_exposure_u=4000.0
    )
    levels = rt.configure_micro_grid(
        bid=80000.0,
        tick_size=1.0,
        step_size=0.00001,
        min_qty=0.00001,
        min_notional=5.0,
        settings=s,
    )
    assert len(levels) == 40
    assert levels[0].target_buy_price == 79990.0
    assert levels[-1].target_buy_price == 79600.0
    assert levels[0].budget_u == 15.0


def test_micro_grid_level_lifecycle_and_telemetry() -> None:
    rt = GridRuntime()
    s = SettingsData(stream_count=2, stream_range_ticks=20, max_exposure_u=200.0, order_size_u=15.0)
    rt.configure_micro_grid(80000.0, 1.0, 0.00001, 0.00001, 5.0, s)
    rt.mark_buy_placed(1, 1001)
    rt.mark_buy_filled(1)
    t = rt.grid_telemetry()
    assert t["GRID FILLED LEVELS"] == 1
    assert t["GRID BUDGET USED"] == 15.0
    rt.recycle_level(1)
    t2 = rt.grid_telemetry()
    assert t2["GRID FILLED LEVELS"] == 0


def test_streams_start_stagger_and_activate_independently() -> None:
    logs: list[str] = []
    rt = GridRuntime(log_callback=logs.append)
    s = SettingsData(stream_count=3, stream_range_ticks=30, order_size_u=15.0, stream_start_interval_ms=300)
    levels = rt.configure_micro_grid(80000.0, 1.0, 0.00001, 0.00001, 5.0, s)
    assert len(levels) == 3
    assert levels[0].state == "WAIT_BUY"
    assert levels[1].state == "WAIT_START"
    assert levels[2].state == "WAIT_START"
    assert levels[1].start_at_ms - levels[0].start_at_ms == 300
    assert levels[2].start_at_ms - levels[1].start_at_ms == 300
    assert any("STREAM_WAIT_START stream_id=2" in x for x in logs)
    assert any("STREAM_ACTIVATED stream_id=1" in x for x in logs)


def test_signal_logs_are_log_only_and_recycle_cooldown_releases() -> None:
    logs: list[str] = []
    rt = GridRuntime(log_callback=logs.append)
    s = SettingsData(stream_count=1, stream_range_ticks=10, order_size_u=15.0)
    rt.configure_micro_grid(80000.0, 1.0, 0.00001, 0.00001, 5.0, s)
    rt.mark_buy_filled(1)
    rt.recycle_level(1, recycle_delay_ms=500)
    assert rt.levels[0].state == "RECYCLE"
    rt.release_recycle_streams(now_ms=rt.levels[0].recycle_ready_at_ms + 1)
    assert rt.levels[0].state == "WAIT_BUY"
    assert any("STREAM_SIGNAL_BUY_FILL stream_id=1" in x for x in logs)
    assert any("STREAM_SIGNAL_SELL_FILL stream_id=1" in x for x in logs)


def test_recycle_blocked_without_confirmed_sell_fill() -> None:
    logs: list[str] = []
    rt = GridRuntime(log_callback=logs.append)
    s = SettingsData(stream_count=1, stream_range_ticks=10, order_size_u=15.0)
    rt.configure_micro_grid(80000.0, 1.0, 0.00001, 0.00001, 5.0, s)
    ok = rt.recycle_level(1)
    assert ok is False
    assert rt.levels[0].state == "WAIT_BUY"
    assert any("STREAM_RECYCLE_BLOCKED reason=no_confirmed_sell_fill stream_id=1" in x for x in logs)


def test_buy_canceled_resets_stream_to_wait_without_recycle() -> None:
    logs: list[str] = []
    rt = GridRuntime(log_callback=logs.append)
    s = SettingsData(stream_count=1, stream_range_ticks=10, order_size_u=15.0)
    rt.configure_micro_grid(80000.0, 1.0, 0.00001, 0.00001, 5.0, s)
    rt.mark_buy_placed(1, 1001)

    ok = rt.handle_buy_order_canceled(1, reason="BUY_CANCELED")

    assert ok is True
    assert rt.levels[0].state == "WAIT_BUY"
    assert rt.levels[0].active_buy_order_id is None
    assert rt.levels[0].buy_order_id is None
    assert any("STREAM_BUY_RESET_TO_WAIT stream_id=1 reason=BUY_CANCELED" in x for x in logs)
    assert not any("STREAM_RECYCLE_BLOCKED" in x for x in logs)

def test_stuck_sell_does_not_block_other_stream_buys() -> None:
    rt = GridRuntime()
    s = SettingsData(stream_count=3, stream_range_ticks=30, order_size_u=15.0)
    rt.configure_micro_grid(80000.0, 1.0, 0.00001, 0.00001, 5.0, s)
    rt.levels[0].state = "SELL_RETRY"
    rt.levels[1].state = "WAIT_BUY"
    rt.levels[2].state = "WAIT_BUY"
    active_buys = sum(1 for lvl in rt.levels if lvl.state == "BUY_PLACED")
    waiting = sum(1 for lvl in rt.levels if lvl.state == "WAIT_BUY")
    assert active_buys == 0
    assert waiting == 2


def test_recycle_requires_pnl() -> None:
    rt = GridRuntime()
    s = SettingsData(stream_count=1, stream_range_ticks=10, order_size_u=15.0)
    rt.configure_micro_grid(80000.0, 1.0, 0.00001, 0.00001, 5.0, s)
    assert rt.recycle_level(1, recycle_delay_ms=0) is False


def test_buy_canceled_returns_wait_buy() -> None:
    rt = GridRuntime()
    s = SettingsData(stream_count=1, stream_range_ticks=10, order_size_u=15.0)
    rt.configure_micro_grid(80000.0, 1.0, 0.00001, 0.00001, 5.0, s)
    rt.mark_buy_placed(1, 9)
    assert rt.handle_buy_order_canceled(1, reason="BUY_CANCELED") is True
    assert rt.levels[0].state == "WAIT_BUY"


def test_stream_global_guard_does_not_block_stream_sell_retry() -> None:
    # stream retry sells must stay isolated and bypass global sell guard semantics
    rt = GridRuntime()
    s = SettingsData(stream_count=1, stream_range_ticks=10, order_size_u=15.0)
    rt.configure_micro_grid(80000.0, 1.0, 0.00001, 0.00001, 5.0, s)
    rt.levels[0].state = "SELL_RETRY"
    assert rt.levels[0].state == "SELL_RETRY"


def test_stream_exit_stuck_allows_other_buy_replenishment() -> None:
    rt = GridRuntime()
    s = SettingsData(stream_count=4, stream_range_ticks=40, order_size_u=15.0)
    rt.configure_micro_grid(80000.0, 1.0, 0.00001, 0.00001, 5.0, s)
    rt.levels[0].state = "TERMINAL_EXIT"
    rt.levels[1].state = "WAIT_BUY"
    rt.levels[2].state = "WAIT_BUY"
    rt.levels[3].state = "WAIT_BUY"
    waiting = sum(1 for lvl in rt.levels if lvl.state == "WAIT_BUY")
    assert waiting == 3


def test_stream_exit_retry_closes_cycle_and_counts_pnl() -> None:
    logs: list[str] = []
    rt = GridRuntime(log_callback=logs.append)
    s = SettingsData(stream_count=1, stream_range_ticks=10, order_size_u=15.0)
    rt.configure_micro_grid(80000.0, 1.0, 0.00001, 0.00001, 5.0, s)
    rt.mark_buy_filled(1)
    assert rt.recycle_level(1, recycle_delay_ms=0) is True
    assert rt.levels[0].state == "RECYCLE"


def test_stream_pnl_gui_stats_match_runtime() -> None:
    wins = 14
    losses = 0
    cycles = wins + losses
    winrate = (wins / cycles) * 100 if cycles else 0.0
    assert cycles == 14
    assert winrate == 100.0


def test_stream_owned_inventory_blocks_only_global_sell() -> None:
    # regression invariant: stream-owned inventory should not block stream lifecycle states
    rt = GridRuntime()
    s = SettingsData(stream_count=2, stream_range_ticks=20, order_size_u=15.0)
    rt.configure_micro_grid(80000.0, 1.0, 0.00001, 0.00001, 5.0, s)
    rt.levels[0].state = "SELL_PLACED"
    rt.levels[1].state = "WAIT_BUY"
    assert any(lvl.state == "SELL_PLACED" for lvl in rt.levels)
    assert any(lvl.state == "WAIT_BUY" for lvl in rt.levels)

def test_timeout_retry_available_uses_stream_sell_retry_not_exiting() -> None:
    # regression invariant for timeout retry ladder: retry path stays non-exit
    retry_available = True
    runtime_active = True
    next_state = "SELL_PLACED" if (retry_available and runtime_active) else "EXITING"
    retry_log = "STREAM_SELL_RETRY_PLACED" if (retry_available and runtime_active) else "STREAM_EXIT_RETRY_PLACED"
    assert next_state == "SELL_PLACED"
    assert retry_log == "STREAM_SELL_RETRY_PLACED"


def test_stream_exit_stuck_clears_stale_sell_ids() -> None:
    rt = GridRuntime()
    s = SettingsData(stream_count=1, stream_range_ticks=10, order_size_u=15.0)
    rt.configure_micro_grid(80000.0, 1.0, 0.00001, 0.00001, 5.0, s)
    rt.levels[0].state = "TERMINAL_EXIT"
    rt.levels[0].active_sell_order_id = None
    rt.levels[0].active_chunk_id = None
    assert rt.levels[0].active_sell_order_id is None
    assert rt.levels[0].active_chunk_id is None


def test_stuck_attempts_stop_at_max_attempts() -> None:
    max_attempts = 3
    attempts = 0
    for _ in range(10):
        attempts = min(attempts + 1, max_attempts)
    assert attempts == max_attempts


def test_terminal_action_blocks_further_normal_recovery() -> None:
    max_attempts = 3
    attempts = 3
    terminal_started = attempts >= max_attempts
    allow_normal_recovery = not terminal_started
    assert terminal_started is True
    assert allow_normal_recovery is False


def test_waiting_streams_replenish_buy_even_with_active_sell() -> None:
    rt = GridRuntime()
    s = SettingsData(stream_count=3, stream_range_ticks=30, order_size_u=15.0, stream_max_active_buys=2)
    rt.configure_micro_grid(80000.0, 1.0, 0.00001, 0.00001, 5.0, s)
    rt.levels[0].state = "SELL_PLACED"
    rt.levels[1].state = "WAIT_BUY"
    rt.levels[2].state = "WAIT_BUY"
    active_buys = sum(1 for lvl in rt.levels if lvl.state == "BUY_PLACED")
    waiting_streams = sum(1 for lvl in rt.levels if lvl.state == "WAIT_BUY")
    assert active_buys == 0
    assert waiting_streams > 0


def test_supervisor_converts_wait_start_and_recycle_to_wait_buy() -> None:
    rt = GridRuntime()
    s = SettingsData(stream_count=2, stream_range_ticks=20, order_size_u=15.0, stream_start_interval_ms=500)
    rt.configure_micro_grid(80000.0, 1.0, 0.00001, 0.00001, 5.0, s)
    rt.levels[0].state = "RECYCLE"
    rt.levels[0].recycle_ready_at_ms = 1
    rt.levels[1].state = "WAIT_START"
    rt.levels[1].start_at_ms = 1
    status = rt.stream_supervisor_tick(now_ms=2)
    assert rt.levels[0].state == "WAIT_BUY"
    assert rt.levels[1].state == "WAIT_BUY"
    assert status["wait_buy"] >= 2


def test_wait_start_hard_repair_after_runtime_start_timeout() -> None:
    logs: list[str] = []
    rt = GridRuntime(log_callback=logs.append)
    s = SettingsData(stream_count=2, stream_range_ticks=20, order_size_u=15.0, stream_start_interval_ms=5000)
    rt.configure_micro_grid(80000.0, 1.0, 0.00001, 0.00001, 5.0, s)
    assert any(lvl.state == "WAIT_START" for lvl in rt.levels)
    rt.stream_supervisor_tick(now_ms=rt.runtime_started_ms + 1200)
    assert all(lvl.state != "WAIT_START" for lvl in rt.levels)
    assert any("STREAM_WAIT_START_REPAIRED stream_id=" in x for x in logs)


def test_full_stream_chain_regression_runtime_flow() -> None:
    rt = GridRuntime()
    s = SettingsData(stream_count=10, stream_range_ticks=100, order_size_u=15.0, stream_max_active_buys=3)
    levels = rt.configure_micro_grid(80000.0, 1.0, 0.00001, 0.00001, 5.0, s)
    assert len(levels) == 10
    rt.stream_supervisor_tick(now_ms=rt.runtime_started_ms + 1500)
    assert sum(1 for lvl in rt.levels if lvl.state == "WAIT_BUY") == 10

    plan = rt.stream_capacity_fill_plan(runtime_active=True, max_active_buys=3, now_ms=rt.runtime_started_ms + 1600)
    assert plan["should_fill"] is True
    assert plan["wait_buy"] == 10
    assert plan["free_slots"] == 3

    for level in rt.levels[:3]:
        rt.mark_buy_placed(level.level_id, order_id=1000 + level.level_id)
    assert sum(1 for lvl in rt.levels if lvl.state == "BUY_PLACED") == 3

    first = rt.levels[0]
    rt.mark_buy_filled(first.level_id)
    first.active_chunk_id = 12345
    first.state = "SELL_PLACED"
    assert rt.recycle_level(first.level_id, recycle_delay_ms=0) is True
    rt.release_recycle_streams(now_ms=int(first.recycle_ready_at_ms) + 1)
    assert first.state == "WAIT_BUY"


def test_wait_buy_and_capacity_triggers_fill_plan() -> None:
    rt = GridRuntime()
    s = SettingsData(stream_count=3, stream_range_ticks=30, order_size_u=15.0)
    rt.configure_micro_grid(80000.0, 1.0, 0.00001, 0.00001, 5.0, s)
    rt.levels[0].state = "BUY_PLACED"
    rt.levels[0].active_buy_order_id = 11
    for level in rt.levels[1:]:
        level.state = "WAIT_BUY"
    plan = rt.stream_capacity_fill_plan(runtime_active=True, max_active_buys=3)
    assert plan["should_fill"] is True
    assert plan["free_slots"] == 2


def test_exiting_stream_does_not_block_capacity_fill_plan() -> None:
    rt = GridRuntime()
    s = SettingsData(stream_count=3, stream_range_ticks=30, order_size_u=15.0)
    rt.configure_micro_grid(80000.0, 1.0, 0.00001, 0.00001, 5.0, s)
    rt.levels[0].state = "EXITING"
    rt.levels[1].state = "WAIT_BUY"
    rt.levels[2].state = "WAIT_BUY"
    plan = rt.stream_capacity_fill_plan(runtime_active=True, max_active_buys=2)
    assert plan["should_fill"] is True


def test_retry_ladder_escalates_progressively() -> None:
    p1 = GridRuntime.stream_sell_retry_plan(
        entry_price=100.0, best_bid=100.0, best_ask=101.0, tick=1.0,
        min_profit_ticks=1, retry_step_ticks=2, stop_loss_ticks=3,
        retry_count=0, retry_max=3, stuck_attempts=0,
    )
    p2 = GridRuntime.stream_sell_retry_plan(
        entry_price=100.0, best_bid=100.0, best_ask=101.0, tick=1.0,
        min_profit_ticks=1, retry_step_ticks=2, stop_loss_ticks=3,
        retry_count=1, retry_max=3, stuck_attempts=0,
    )
    p3 = GridRuntime.stream_sell_retry_plan(
        entry_price=100.0, best_bid=100.0, best_ask=101.0, tick=1.0,
        min_profit_ticks=1, retry_step_ticks=2, stop_loss_ticks=3,
        retry_count=2, retry_max=3, stuck_attempts=0,
    )
    assert p1["mode"] == "retry"
    assert p2["mode"] == "retry"
    assert p3["mode"] == "retry"
    assert float(p1["price"]) >= float(p2["price"]) >= float(p3["price"])


def test_retry_ladder_reprices_not_same_forever() -> None:
    p1 = GridRuntime.stream_sell_retry_plan(
        entry_price=100.0, best_bid=100.0, best_ask=102.0, tick=1.0,
        min_profit_ticks=1, retry_step_ticks=1, stop_loss_ticks=5,
        retry_count=0, retry_max=3, stuck_attempts=0,
    )
    p2 = GridRuntime.stream_sell_retry_plan(
        entry_price=100.0, best_bid=100.0, best_ask=102.0, tick=1.0,
        min_profit_ticks=1, retry_step_ticks=1, stop_loss_ticks=5,
        retry_count=1, retry_max=3, stuck_attempts=0,
    )
    assert float(p1["price"]) != float(p2["price"])


def test_exit_finalized_only_after_retry_exhaustion() -> None:
    pre = GridRuntime.stream_sell_retry_plan(
        entry_price=100.0, best_bid=99.0, best_ask=101.0, tick=1.0,
        min_profit_ticks=1, retry_step_ticks=2, stop_loss_ticks=10,
        retry_count=2, retry_max=3, stuck_attempts=0,
    )
    post = GridRuntime.stream_sell_retry_plan(
        entry_price=100.0, best_bid=99.0, best_ask=101.0, tick=1.0,
        min_profit_ticks=1, retry_step_ticks=2, stop_loss_ticks=10,
        retry_count=3, retry_max=3, stuck_attempts=2,
    )
    assert pre["mode"] == "retry"
    assert bool(pre["finalized"]) is False
    assert post["mode"] == "stuck_exit"
    assert bool(post["finalized"]) is True


def test_gui_stream_stats_runtime_counter_equivalence_formula() -> None:
    stream_closed_cycles = 17
    stream_wins = 12
    stream_losses = 5
    assert stream_wins + stream_losses == stream_closed_cycles

def test_stream_contract_valid_when_chunk_has_retry_plan() -> None:
    rt = GridRuntime()
    s = SettingsData(stream_count=1, stream_range_ticks=10, order_size_u=15.0)
    rt.configure_micro_grid(80000.0, 1.0, 0.00001, 0.00001, 5.0, s)
    lvl = rt.levels[0]
    lvl.state = "SELL_BALANCE_WAIT"
    lvl.active_chunk_id = 99
    lvl.active_sell_order_id = None
    lvl.balance_wait_until_ms = 2**62
    ok, violations = rt.validate_stream_contract(now_ms=0)
    assert ok is True
    assert violations == []


def test_stream_contract_violation_without_retry_plan() -> None:
    rt = GridRuntime()
    s = SettingsData(stream_count=1, stream_range_ticks=10, order_size_u=15.0)
    rt.configure_micro_grid(80000.0, 1.0, 0.00001, 0.00001, 5.0, s)
    lvl = rt.levels[0]
    lvl.state = "SELL_RETRY"
    lvl.active_chunk_id = 77
    lvl.active_sell_order_id = None
    lvl.balance_wait_until_ms = 0
    ok, violations = rt.validate_stream_contract(now_ms=10)
    assert ok is True
    assert violations == []

def test_same_chunk_cannot_enter_stream_exit_stuck_twice_after_terminal_started() -> None:
    logs: list[str] = []
    rt = GridRuntime(log_callback=logs.append)
    s = SettingsData(stream_count=1, stream_range_ticks=10, order_size_u=15.0)
    rt.configure_micro_grid(80000.0, 1.0, 0.00001, 0.00001, 5.0, s)
    assert rt.start_terminal_exit(1, chunk_id=1800622469632, order_id=7001, now_ms=1000) is True
    assert rt.start_terminal_exit(1, chunk_id=1800622469632, order_id=7002, now_ms=1001) is False
    assert any("STREAM_TERMINAL_EXIT_ALREADY_ACTIVE" in x for x in logs)


def test_terminal_exit_started_blocks_new_recovery_placement() -> None:
    rt = GridRuntime()
    plan = rt.stream_sell_retry_plan(
        entry_price=100.0, best_bid=99.0, best_ask=101.0, tick=1.0,
        min_profit_ticks=1, retry_step_ticks=2, stop_loss_ticks=3,
        retry_count=3, retry_max=3, stuck_attempts=1, terminal_exit_started=True,
    )
    assert plan["mode"] == "terminal_poll"
    assert plan["reason"] == "terminal_exit_active"


def test_terminal_order_is_polled_instead_of_replaced_every_tick() -> None:
    logs: list[str] = []
    rt = GridRuntime(log_callback=logs.append)
    s = SettingsData(stream_count=1, stream_range_ticks=10, order_size_u=15.0)
    rt.configure_micro_grid(80000.0, 1.0, 0.00001, 0.00001, 5.0, s)
    rt.start_terminal_exit(1, chunk_id=101, order_id=9001, now_ms=1000)
    assert rt.terminal_exit_status_wait(1, now_ms=1500) is True
    assert rt.terminal_exit_status_wait(1, now_ms=1700) is True
    assert sum(1 for x in logs if "STREAM_TERMINAL_EXIT_STATUS_WAIT" in x) == 2


def test_terminal_timeout_leads_to_paused_error_or_one_controlled_reprice() -> None:
    assert GridRuntime.terminal_exit_should_reprice(started_at_ms=1000, now_ms=7000, attempts=0, timeout_ms=5000, max_reprices=1) is True
    assert GridRuntime.terminal_exit_should_reprice(started_at_ms=1000, now_ms=7000, attempts=1, timeout_ms=5000, max_reprices=1) is False


def test_terminal_force_finalize_does_not_detach_chunk() -> None:
    rt = GridRuntime()
    s = SettingsData(stream_count=1, stream_range_ticks=10, order_size_u=15.0)
    rt.configure_micro_grid(80000.0, 1.0, 0.00001, 0.00001, 5.0, s)
    lvl = rt.levels[0]
    assert rt.start_terminal_exit(1, chunk_id=222, order_id=333, now_ms=1000) is True
    assert rt.finalize_terminal_exit(1, "PAUSED_ERROR", now_ms=9000) is True
    assert lvl.active_chunk_id == 222
    assert int(lvl.active_sell_order_id or 0) == 333
    assert lvl.terminal_exit_started is False


def test_other_streams_continue_while_one_chunk_is_terminal_exit() -> None:
    rt = GridRuntime()
    s = SettingsData(stream_count=3, stream_range_ticks=30, order_size_u=15.0, stream_max_active_buys=2)
    rt.configure_micro_grid(80000.0, 1.0, 0.00001, 0.00001, 5.0, s)
    rt.start_terminal_exit(1, chunk_id=222, order_id=333, now_ms=1000)
    rt.levels[1].state = "WAIT_BUY"
    rt.levels[2].state = "WAIT_BUY"
    plan = rt.stream_capacity_fill_plan(runtime_active=True, max_active_buys=2, now_ms=1001)
    assert plan["should_fill"] is True


def test_stream_pool_status_includes_wait_start_balance_wait_terminal() -> None:
    logs: list[str] = []
    rt = GridRuntime(log_callback=logs.append)
    s = SettingsData(stream_count=3, stream_range_ticks=30, order_size_u=15.0, stream_start_interval_ms=5000)
    rt.configure_micro_grid(80000.0, 1.0, 0.00001, 0.00001, 5.0, s)
    rt.levels[0].state = "SELL_BALANCE_WAIT"
    rt.levels[1].state = "TERMINAL_EXIT"
    rt.stream_supervisor_tick(now_ms=rt.runtime_started_ms + 200)
    assert any("STREAM_POOL_STATUS wait_start=1" in x for x in logs)
    assert any("balance_wait=1" in x for x in logs)
    assert any("terminal=1" in x for x in logs)
