from __future__ import annotations


def score_config(stats: dict) -> float:
    pnl = float(stats.get("pnl", 0.0))
    cycles = int(stats.get("cycles", 0))
    wins = int(stats.get("wins", 0))
    losses = int(stats.get("losses", 0))
    stuck = int(stats.get("exit_stuck", 0))
    timeouts = int(stats.get("timeouts", 0))
    params = stats.get("params", {})
    winrate = (wins / cycles) if cycles else 0.0
    winrate_bonus = winrate * 30.0
    stable_cycles_bonus = min(cycles, 1200) * 0.03
    losses_penalty = losses * 2.0
    stuck_penalty = stuck * 6.0
    timeout_penalty = timeouts * 2.2
    over_aggressive_penalty = 0.0
    too_many_streams_penalty = 0.0
    if int(params.get("stream_target_ticks", 20)) < int(params.get("stream_min_profit_ticks", 8)) + 4:
        over_aggressive_penalty += 8.0
    if int(params.get("stream_sell_timeout_ms", 2000)) < 1200:
        over_aggressive_penalty += 5.0
    if int(params.get("stop_loss_ticks", 120)) < 80:
        over_aggressive_penalty += 6.0
    stream_count = int(params.get("stream_count", 10))
    active_buys = int(params.get("stream_max_active_buys", 4))
    if stream_count > 28:
        too_many_streams_penalty += (stream_count - 28) * 0.4
    if active_buys > 14:
        too_many_streams_penalty += (active_buys - 14) * 0.7
    return pnl + winrate_bonus + stable_cycles_bonus - losses_penalty - stuck_penalty - timeout_penalty - over_aggressive_penalty - too_many_streams_penalty
