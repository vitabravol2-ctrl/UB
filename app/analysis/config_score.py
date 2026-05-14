from __future__ import annotations


def score_config(stats: dict) -> float:
    pnl = float(stats.get("pnl", 0.0))
    cycles = int(stats.get("cycles", 0))
    wins = int(stats.get("wins", 0))
    losses = int(stats.get("losses", 0))
    stuck = int(stats.get("exit_stuck", 0))
    timeouts = int(stats.get("timeouts", 0))
    winrate = (wins / cycles) if cycles else 0.0
    return pnl + winrate * 25.0 + cycles * 0.02 - stuck * 4.0 - timeouts * 2.0 - losses * 1.5
