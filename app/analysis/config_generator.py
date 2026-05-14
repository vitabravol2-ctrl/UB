from __future__ import annotations

import random
from dataclasses import asdict
from typing import Any

PROFILE_CLASSES: tuple[str, ...] = ("FAST_SCALPER", "BALANCED", "SURVIVAL", "HIGH_FLOW", "LOW_RISK")
PROFILE_CLASS_BY_TOURNAMENT: dict[str, tuple[str, ...]] = {
    "FAST": ("FAST_SCALPER", "HIGH_FLOW", "BALANCED"),
    "SAFE": ("SURVIVAL", "LOW_RISK", "BALANCED"),
    "BALANCED": ("BALANCED", "SURVIVAL", "FAST_SCALPER"),
}
MARKET_MODES: tuple[str, ...] = ("WIDE_SPREAD", "TIGHT_SPREAD", "FAST_TAPE", "SLOW_TAPE", "VOLATILE", "STABLE")

MUTATION_GROUPS: dict[str, tuple[str, ...]] = {
    "ENTRY_ONLY": (
        "min_spread_ticks", "entry_offset_ticks", "buy_timeout_ms", "buy_timeout_ms_fast", "buy_watchdog_ms", "far_buy_ticks",
        "entry_reprice_cooldown_ms", "max_entry_reprices", "entry_chase_ticks", "entry_cross_if_spread_ticks_above",
        "min_spread_after_entry_ticks", "stream_buy_max_distance_ticks",
    ),
    "SELL_ONLY": (
        "stream_target_ticks", "stream_min_profit_ticks", "stream_sell_timeout_ms", "stream_sell_retry_max", "stream_sell_retry_step_ticks",
        "sell_watchdog_ms", "far_sell_ticks", "sell_reprice_cooldown_ms", "aggressive_exit_offset", "max_sell_reprices",
        "exit_stage1_ms", "exit_stage2_ms", "exit_stage3_ms", "exit_reprice_step_ticks", "exit_max_reprices",
    ),
    "PANIC_ONLY": (
        "stop_loss_ticks", "panic_ladder_step_ticks", "panic_ladder_ms", "panic_hold_max_ms", "panic_cross_after_ms",
        "taker_exit_after_ms", "taker_exit_max_slippage_ticks", "taker_exit_force_flat_after_ms", "stream_loss_cooldown_ms",
    ),
    "SCALE_ONLY": (
        "stream_count", "stream_max_active_buys", "stream_place_interval_ms", "stream_start_interval_ms", "stream_recycle_delay_ms",
        "stream_max_inventory_u", "stream_pause_buy_inventory_u",
    ),
}

PRIORITY_PARAMS: tuple[str, ...] = (
    "stream_target_ticks", "stream_min_profit_ticks", "stream_sell_timeout_ms", "stream_sell_retry_max", "stream_sell_retry_step_ticks",
    "buy_timeout_ms_fast", "entry_chase_ticks", "stream_max_active_buys", "stream_recycle_delay_ms", "min_spread_ticks",
)

MUTABLE_PARAMS: dict[str, tuple[int, int, int]] = {
    "min_spread_ticks": (1, 24, 1), "entry_offset_ticks": (0, 12, 1), "buy_timeout_ms": (200, 2500, 50), "buy_timeout_ms_fast": (100, 800, 20),
    "buy_watchdog_ms": (300, 4000, 50), "far_buy_ticks": (1, 35, 1), "entry_reprice_cooldown_ms": (50, 1500, 25), "max_entry_reprices": (0, 10, 1),
    "entry_chase_ticks": (0, 12, 1), "entry_cross_if_spread_ticks_above": (1, 25, 1), "min_spread_after_entry_ticks": (0, 14, 1), "stream_buy_max_distance_ticks": (1, 60, 1),
    "stream_target_ticks": (10, 180, 1), "stream_min_profit_ticks": (1, 25, 1), "stream_sell_timeout_ms": (800, 6000, 100), "stream_sell_retry_max": (1, 8, 1),
    "stream_sell_retry_step_ticks": (1, 12, 1), "sell_watchdog_ms": (300, 5000, 50), "far_sell_ticks": (1, 50, 1), "sell_reprice_cooldown_ms": (50, 1500, 25),
    "aggressive_exit_offset": (0, 20, 1), "max_sell_reprices": (0, 10, 1), "exit_stage1_ms": (100, 3000, 50), "exit_stage2_ms": (200, 5000, 50),
    "exit_stage3_ms": (300, 7000, 50), "exit_reprice_step_ticks": (1, 12, 1), "exit_max_reprices": (0, 12, 1),
    "stop_loss_ticks": (50, 500, 5), "panic_ladder_step_ticks": (1, 30, 1), "panic_ladder_ms": (100, 4000, 50), "panic_hold_max_ms": (100, 6000, 50),
    "panic_cross_after_ms": (100, 4000, 50), "taker_exit_after_ms": (500, 4000, 50), "taker_exit_max_slippage_ticks": (1, 30, 1), "taker_exit_force_flat_after_ms": (500, 8000, 50),
    "stream_loss_cooldown_ms": (0, 5000, 50), "stream_count": (5, 40, 1), "stream_max_active_buys": (2, 20, 1), "stream_place_interval_ms": (25, 800, 25),
    "stream_start_interval_ms": (25, 1000, 25), "stream_recycle_delay_ms": (50, 1000, 25), "stream_max_inventory_u": (1, 200, 1), "stream_pause_buy_inventory_u": (1, 200, 1),
}


def _clamp_int(value: int, lo: int, hi: int, step: int) -> int:
    v = max(lo, min(hi, value))
    return lo + ((v - lo) // step) * step


def base_payload(settings: Any) -> dict[str, Any]:
    return asdict(settings)


def _class_biased_keys(profile_class: str) -> tuple[str, ...]:
    if profile_class == "FAST_SCALPER":
        return ("stream_sell_timeout_ms", "stream_target_ticks", "stream_max_active_buys", "buy_timeout_ms_fast", "entry_chase_ticks")
    if profile_class == "SURVIVAL":
        return ("stream_sell_timeout_ms", "stream_min_profit_ticks", "stream_recycle_delay_ms", "min_spread_ticks", "stop_loss_ticks")
    if profile_class == "HIGH_FLOW":
        return ("stream_max_active_buys", "stream_count", "stream_recycle_delay_ms", "stream_place_interval_ms")
    if profile_class == "LOW_RISK":
        return ("min_spread_ticks", "stream_min_profit_ticks", "stream_sell_retry_max", "stream_sell_timeout_ms")
    return PRIORITY_PARAMS


def mutate_from(base: dict[str, Any], rng: random.Random, intensity: float = 1.0, mutation_type: str = "MIXED_SMALL", profile_class: str = "BALANCED", generation: int = 0) -> dict[str, Any]:
    out = dict(base)
    tier_roll = rng.random()
    if tier_roll < 0.90:
        mutation_tier, tier_scale = "SMALL", 0.6
    elif tier_roll < 0.99:
        mutation_tier, tier_scale = "MEDIUM", 1.5
    else:
        mutation_tier, tier_scale = "CRAZY", 5.0
    mutate_count = rng.randint(1, 3)
    pools = list(dict.fromkeys([*PRIORITY_PARAMS, *_class_biased_keys(profile_class), *MUTABLE_PARAMS.keys()]))
    keys = rng.sample(pools, k=min(mutate_count, len(pools)))
    for key in keys:
        if key not in MUTABLE_PARAMS:
            continue
        lo, hi, step = MUTABLE_PARAMS[key]
        cur = int(out.get(key, lo))
        delta_steps = max(1, int((hi - lo) / (24 * step) * intensity * tier_scale))
        shift = rng.randint(-delta_steps, delta_steps) * step
        out[key] = _clamp_int(cur + shift, lo, hi, step)
    out["mutation_type"] = mutation_type
    out["mutation_tier"] = mutation_tier
    out["profile_class"] = profile_class
    out["generation"] = generation
    out["order_size_u"] = base.get("order_size_u")
    return out


def generate_initial(base: dict[str, Any], count: int, seed: int = 42) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    return [mutate_from(base, rng, intensity=1.0, profile_class=rng.choice(PROFILE_CLASSES)) for _ in range(count)]
