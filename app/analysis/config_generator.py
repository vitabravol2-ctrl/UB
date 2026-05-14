from __future__ import annotations

import random
from dataclasses import asdict
from typing import Any

MUTABLE_PARAMS: dict[str, tuple[int, int, int]] = {
    "stream_target_ticks": (8, 64, 2),
    "stream_min_profit_ticks": (2, 24, 1),
    "stream_sell_timeout_ms": (800, 8000, 100),
    "stream_sell_retry_max": (0, 8, 1),
    "stream_sell_retry_step_ticks": (1, 10, 1),
    "buy_timeout_ms_fast": (80, 1200, 20),
    "min_spread_ticks": (1, 24, 1),
    "entry_chase_ticks": (0, 8, 1),
    "stream_max_active_buys": (1, 8, 1),
    "stream_recycle_delay_ms": (0, 3000, 50),
}


def _clamp_int(value: int, lo: int, hi: int, step: int) -> int:
    v = max(lo, min(hi, value))
    return lo + ((v - lo) // step) * step


def base_payload(settings: Any) -> dict[str, Any]:
    return asdict(settings)


def mutate_from(base: dict[str, Any], rng: random.Random, intensity: float = 1.0) -> dict[str, Any]:
    out = dict(base)
    for key, (lo, hi, step) in MUTABLE_PARAMS.items():
        cur = int(out.get(key, lo))
        delta_steps = max(1, int((hi - lo) / (10 * step) * intensity))
        shift = rng.randint(-delta_steps, delta_steps) * step
        out[key] = _clamp_int(cur + shift, lo, hi, step)
    out["order_size_u"] = base.get("order_size_u")
    return out


def generate_initial(base: dict[str, Any], count: int, seed: int = 42) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    return [mutate_from(base, rng, intensity=1.4) for _ in range(count)]
