from app.core.price_ticks import add_ticks, price_distance_ticks, round_price_to_tick, spread_ticks, sub_ticks


def test_add_sub_ticks() -> None:
    assert add_ticks(100.0, 5, 0.1) == 100.5
    assert sub_ticks(100.0, 5, 0.1) == 99.5


def test_spread_and_distance_ticks() -> None:
    assert spread_ticks(100.0, 100.7, 0.1) == 7
    assert price_distance_ticks(101.2, 100.0, 0.1) == 12


def test_round_price_to_tick() -> None:
    assert round_price_to_tick(100.79, 0.1) == 100.7
