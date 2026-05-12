from __future__ import annotations

from decimal import Decimal, ROUND_DOWN


def _d(value: float | int | str | None) -> Decimal:
    return Decimal(str(value or 0))


def price_to_ticks(price: float, tick_size: float) -> int:
    t = _d(tick_size)
    if t <= 0:
        return 0
    return int((_d(price) / t).to_integral_value(rounding=ROUND_DOWN))


def ticks_to_price(ticks: int, tick_size: float) -> float:
    return float(_d(ticks) * _d(tick_size))


def price_distance_ticks(a: float, b: float, tick_size: float) -> int:
    return abs(price_to_ticks(a - b, tick_size))


def round_price_to_tick(price: float, tick_size: float) -> float:
    t = _d(tick_size)
    if t <= 0:
        return float(_d(price))
    return float((_d(price) / t).to_integral_value(rounding=ROUND_DOWN) * t)


def normalize_price(price: float, tick_size: float) -> float:
    return round_price_to_tick(price, tick_size)


def add_ticks(price: float, ticks: int, tick_size: float) -> float:
    return normalize_price(price + ticks_to_price(ticks, tick_size), tick_size)


def sub_ticks(price: float, ticks: int, tick_size: float) -> float:
    return normalize_price(price - ticks_to_price(ticks, tick_size), tick_size)


def spread_ticks(bid: float, ask: float, tick_size: float) -> int:
    return max(0, price_to_ticks(ask - bid, tick_size))
