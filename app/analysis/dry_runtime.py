from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.analysis.config_generator import MARKET_MODES, PROFILE_CLASS_BY_TOURNAMENT, PROFILE_CLASSES, mutate_from
from app.analysis.config_score import score_config


@dataclass
class SimConfig:
    cfg_id: int
    params: dict
    tournament: str = "BALANCED"
    pnl: float = 0.0
    cycles: int = 0
    wins: int = 0
    losses: int = 0
    exit_stuck: int = 0
    timeouts: int = 0
    score: float = 0.0
    updated_ms: int = 0
    history: list[float] = field(default_factory=list)
    parent_ids: tuple[int, ...] = field(default_factory=tuple)

    def as_row(self) -> dict:
        early = sum(self.history[: max(1, len(self.history)//3)])
        recent = sum(self.history[-max(1, len(self.history)//3):]) if self.history else 0.0
        degradation = early - recent
        trend = "PROFILE_STABLE"
        if degradation > abs(early) * 0.2:
            trend = "PROFILE_DEGRADING"
        elif recent > early + abs(early) * 0.1:
            trend = "PROFILE_IMPROVING"
        return {
            "id": self.cfg_id, "pnl": self.pnl, "cycles": self.cycles, "wins": self.wins, "losses": self.losses,
            "exit_stuck": self.exit_stuck, "timeouts": self.timeouts, "winrate": (self.wins / self.cycles * 100.0) if self.cycles else 0.0,
            "score": self.score, "params": self.params, "profile_class": self.params.get("profile_class", "BALANCED"),
            "generation": self.params.get("generation", 0), "mutation_tier": self.params.get("mutation_tier", "SMALL"),
            "lineage": f"P{','.join(map(str, self.parent_ids))}" if self.parent_ids else "SEED", "degradation_state": trend,
            "degradation_score": degradation, "stability_score": max(0.0, 100.0 - abs(degradation) * 3.0 - self.exit_stuck * 2.0), "tournament": self.tournament,
            "avg_pnl_hour": (self.pnl / max(1.0, self.cycles)) * 3600.0, "avg_cycles_hour": self.cycles * 3600.0 / max(1.0, len(self.history)),
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
    _events: list[str] = field(default_factory=list, init=False)
    _tournaments: tuple[str, ...] = ("FAST", "SAFE", "BALANCED")
    _memory_file: Path = field(default=Path("analysis/top_profiles.json"), init=False)

    def __post_init__(self) -> None:
        self._rng = random.Random(self.seed)
        self._load_memory()
        while len(self._configs) < self.active_count:
            t = self._tournaments[len(self._configs) % len(self._tournaments)]
            cls = self._rng.choice(PROFILE_CLASS_BY_TOURNAMENT[t])
            self._configs.append(self._new_config(self.base_settings, tournament=t, profile_class=cls))

    def _new_config(self, base: dict, mutation_type: str = "MIXED_SMALL", intensity: float = 1.0, profile_class: str = "BALANCED", generation: int = 0, tournament: str = "BALANCED", parent_ids: tuple[int, ...] = ()) -> SimConfig:
        params = mutate_from(base, self._rng, intensity=intensity, mutation_type=mutation_type, profile_class=profile_class, generation=generation)
        params["tournament"] = tournament
        c = SimConfig(cfg_id=self._next_id, params=params, tournament=tournament, parent_ids=parent_ids)
        self._next_id += 1
        return c

    def _market_mult(self, mode: str) -> tuple[float, float]:
        return {
            "WIDE_SPREAD": (1.2, 0.95), "TIGHT_SPREAD": (0.9, 1.05), "FAST_TAPE": (1.1, 1.2),
            "SLOW_TAPE": (0.95, 0.8), "VOLATILE": (1.6, 1.0), "STABLE": (0.7, 0.9),
        }[mode]

    def _step_one(self, c: SimConfig) -> None:
        p = c.params
        mode = self._rng.choice(MARKET_MODES)
        vol_mult, cycle_mult = self._market_mult(mode)
        timeout_risk = max(0.0, (1200 - p["stream_sell_timeout_ms"]) / 1200)
        win_prob = min(0.92, max(0.12, 0.5 + (p["stream_target_ticks"] - p["stream_min_profit_ticks"]) * 0.008 - timeout_risk * 0.2))
        cycles_now = max(1, int(min(8, p["stream_max_active_buys"] * cycle_mult)))
        delta_pnl = 0.0
        for _ in range(cycles_now):
            c.cycles += 1
            self.tested_total += 1
            if self._rng.random() < win_prob:
                c.wins += 1
                d = (p["stream_min_profit_ticks"] * 0.3 + self._rng.random() * vol_mult)
            else:
                c.losses += 1
                d = -(p["min_spread_ticks"] * 0.2 + self._rng.random() * (1.0 + vol_mult))
            c.pnl += d
            delta_pnl += d
            if self._rng.random() < (0.015 + timeout_risk * 0.08): c.timeouts += 1
            if self._rng.random() < (0.01 + (0.02 if mode == "VOLATILE" else 0.0)): c.exit_stuck += 1
        c.history.append(delta_pnl)
        if len(c.history) > 180: c.history = c.history[-180:]
        row = c.as_row()
        row["runtime_s"] = max(1.0, time.monotonic() - self._started_at)
        row["pnl_variance"] = (sum((x - (sum(c.history) / max(1, len(c.history)))) ** 2 for x in c.history) / max(1, len(c.history))) if c.history else 0.0
        row["degradation_score"] = max(0.0, row["degradation_score"])
        long_run_penalty = 0.0
        if c.cycles > 100 and (c.exit_stuck > c.cycles * 0.08 or c.timeouts > c.cycles * 0.12):
            long_run_penalty = 30.0
        c.score = score_config(row) - long_run_penalty
        c.updated_ms = int(time.time() * 1000)

    def _load_memory(self) -> None:
        if not self._memory_file.exists():
            return
        try:
            data = json.loads(self._memory_file.read_text(encoding="utf-8"))
            for item in data.get("profiles", [])[:15]:
                params = dict(item.get("params", {}))
                params["profile_class"] = item.get("profile_class", "BALANCED")
                self._configs.append(SimConfig(cfg_id=self._next_id, params=params, pnl=float(item.get("pnl", 0.0)), score=float(item.get("score", 0.0))))
                self._next_id += 1
        except Exception:
            self._events.append("genetic memory load failed")

    def _save_memory(self) -> None:
        top = self.top_rows(20)
        payload = {"saved_at": int(time.time()), "profiles": [{"id": r["id"], "profile_class": r.get("profile_class"), "pnl": r["pnl"], "score": r["score"], "market_strengths": MARKET_MODES[:3], "params": r["params"]} for r in top]}
        self._memory_file.parent.mkdir(parents=True, exist_ok=True)
        self._memory_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def tick(self, budget: int = 30) -> None:
        for _ in range(budget): self._step_one(self._configs[self._rng.randrange(len(self._configs))])
        now = time.monotonic()
        if now - self._last_eliminate >= self.eliminate_every_s and len(self._configs) >= self.eliminate_count + 20:
            self._last_eliminate = now
            self._configs.sort(key=lambda x: (x.score, x.pnl))
            protected_ids = {c.cfg_id for c in sorted(self._configs, key=lambda x: x.score, reverse=True)[:5]}
            removed = 0
            survivors: list[SimConfig] = []
            for c in self._configs:
                if removed < self.eliminate_count and c.cfg_id not in protected_ids:
                    removed += 1
                    continue
                survivors.append(c)
            self._configs = survivors
            self._events.append("PROFILE_PROTECTED_CHAMPION: top-5 shield enabled")
            parents = sorted(self._configs, key=lambda x: x.score, reverse=True)[:20]
            for _ in range(self.eliminate_count):
                a, b = self._rng.choice(parents), self._rng.choice(parents)
                tournament = self._rng.choice(self._tournaments)
                cls = self._rng.choice(PROFILE_CLASS_BY_TOURNAMENT[tournament])
                child_base = dict(a.params if self._rng.random() < 0.5 else b.params)
                child = self._new_config(child_base, profile_class=cls, generation=max(int(a.params.get("generation", 0)), int(b.params.get("generation", 0))) + 1, tournament=tournament, parent_ids=(a.cfg_id, b.cfg_id))
                self._configs.append(child)
            self._save_memory()

    def top_rows(self, limit: int = 100) -> list[dict]:
        rows = [c.as_row() for c in self._configs]
        rows.sort(key=lambda r: (r["score"], r["pnl"]), reverse=True)
        for idx, row in enumerate(rows[:5]): row["champion_state"] = "PROFILE_PROTECTED_CHAMPION"
        return rows[:limit]

    def stats(self) -> dict:
        elapsed = max(0.001, time.monotonic() - self._started_at)
        rows = self.top_rows(limit=min(200, len(self._configs)))
        avg_pnl = sum(r["pnl"] for r in rows) / max(1, len(rows))
        best = rows[0] if rows else {"pnl": 0.0, "winrate": 0.0}
        return {"active": len(self._configs), "tested": self.tested_total, "sim_speed": self.tested_total / elapsed, "runtime_s": elapsed, "best_pnl": best["pnl"], "best_winrate": best["winrate"], "avg_pnl": avg_pnl, "cycles_per_hour": (self.tested_total / elapsed) * 3600.0}

    def pull_events(self) -> list[str]:
        events = self._events[:]
        self._events.clear()
        return events
