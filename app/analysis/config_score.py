from __future__ import annotations


def score_config(stats: dict) -> float:
    pnl = float(stats.get("pnl", 0.0))
    cycles = int(stats.get("cycles", 0))
    wins = int(stats.get("wins", 0))
    losses = int(stats.get("losses", 0))
    stuck = int(stats.get("exit_stuck", 0))
    timeouts = int(stats.get("timeouts", 0))
    runtime_s = float(stats.get("runtime_s", 0.0))
    winrate = (wins / cycles) if cycles else 0.0
    cycles_per_hour = (cycles / max(runtime_s, 1.0)) * 3600.0

    pnl_variance = float(stats.get("pnl_variance", 0.0))
    degradation_score = float(stats.get("degradation_score", 0.0))

    pnl_variance_penalty = min(20.0, pnl_variance * 0.4)
    degradation_penalty = degradation_score * 8.0

    fitness = (
        pnl * 1.0
        + winrate * 25.0
        + cycles_per_hour * 0.02
        - stuck * 4.0
        - timeouts * 2.0
        - losses * 1.5
        - pnl_variance_penalty
        - degradation_penalty
    )
    return fitness
