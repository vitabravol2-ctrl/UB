from __future__ import annotations

import random
import time
from dataclasses import dataclass, field

from app.analysis.config_generator import mutate_from
from app.analysis.config_score import score_config


@dataclass
class SimConfig:
    cfg_id: int
    params: dict
    pnl: float = 0.0
    cycles: int = 0
    wins: int = 0
    losses: int = 0
    exit_stuck: int = 0
    timeouts: int = 0
    score: float = 0.0
    updated_ms: int = 0

    def as_row(self) -> dict:
        return {
            "id": self.cfg_id,
            "pnl": self.pnl,
            "cycles": self.cycles,
            "wins": self.wins,
            "losses": self.losses,
            "exit_stuck": self.exit_stuck,
            "timeouts": self.timeouts,
            "winrate": (self.wins / self.cycles * 100.0) if self.cycles else 0.0,
            "score": self.score,
            "params": self.params,
        }


@dataclass
class DryTournamentRuntime:
    base_settings: dict
    active_count: int = 160
    eliminate_every_s: float = 4.0
    eliminate_count: int = 6
    seed: int = 123
    _rng: random.Random = field(init=False)
    _configs: list[SimConfig] = field(default_factory=list, init=False)
    _next_id: int = field(default=1, init=False)
    _started_at: float = field(default_factory=time.monotonic, init=False)
    _last_eliminate: float = field(default_factory=time.monotonic, init=False)
    tested_total: int = 0

    def __post_init__(self) -> None:
        self._rng = random.Random(self.seed)
        for _ in range(self.active_count):
            self._configs.append(self._new_config(self.base_settings))

    def _new_config(self, base: dict) -> SimConfig:
        c = SimConfig(cfg_id=self._next_id, params=mutate_from(base, self._rng, intensity=1.0))
        self._next_id += 1
        return c

    def _step_one(self, c: SimConfig) -> None:
        p = c.params
        volatility = 0.2 + p["entry_chase_ticks"] * 0.04
        profit_edge = p["stream_target_ticks"] - p["stream_min_profit_ticks"]
        timeout_risk = max(0.0, (1200 - p["stream_sell_timeout_ms"]) / 1200)
        win_prob = min(0.9, max(0.15, 0.5 + profit_edge * 0.01 - timeout_risk * 0.15))
        cycles_now = max(1, min(5, p["stream_max_active_buys"]))
        for _ in range(cycles_now):
            c.cycles += 1
            self.tested_total += 1
            if self._rng.random() < win_prob:
                c.wins += 1
                c.pnl += (p["stream_min_profit_ticks"] * 0.3 + self._rng.random() * volatility)
            else:
                c.losses += 1
                c.pnl -= (p["min_spread_ticks"] * 0.2 + self._rng.random() * (1.0 + volatility))
            if self._rng.random() < (0.015 + timeout_risk * 0.08):
                c.timeouts += 1
            if self._rng.random() < 0.01:
                c.exit_stuck += 1
        c.score = score_config(c.as_row())
        c.updated_ms = int(time.time() * 1000)

    def tick(self, budget: int = 30) -> None:
        if not self._configs:
            return
        for _ in range(budget):
            self._step_one(self._configs[self._rng.randrange(len(self._configs))])
        now = time.monotonic()
        if now - self._last_eliminate >= self.eliminate_every_s and len(self._configs) >= self.eliminate_count + 10:
            self._last_eliminate = now
            self._configs.sort(key=lambda x: (x.score, x.pnl))
            self._configs = self._configs[self.eliminate_count:]
            parents = sorted(self._configs, key=lambda x: (x.score, x.pnl), reverse=True)[:20]
            for _ in range(self.eliminate_count):
                parent = self._rng.choice(parents)
                self._configs.append(self._new_config(parent.params))

    def top_rows(self, limit: int = 100) -> list[dict]:
        rows = [c.as_row() for c in self._configs]
        rows.sort(key=lambda r: (r["score"], r["pnl"]), reverse=True)
        return rows[:limit]

    def stats(self) -> dict:
        elapsed = max(0.001, time.monotonic() - self._started_at)
        rows = self.top_rows(limit=min(200, len(self._configs)))
        avg_pnl = sum(r["pnl"] for r in rows) / max(1, len(rows))
        best = rows[0] if rows else {"pnl": 0.0, "winrate": 0.0}
        return {
            "active": len(self._configs),
            "tested": self.tested_total,
            "sim_speed": self.tested_total / elapsed,
            "runtime_s": elapsed,
            "best_pnl": best["pnl"],
            "best_winrate": best["winrate"],
            "avg_pnl": avg_pnl,
            "cycles_per_hour": (self.tested_total / elapsed) * 3600.0,
        }
