from app.core.binance_account import round_price_to_tick, round_qty_to_step, validate_min_notional, validate_min_qty


def test_round_price_to_tick() -> None:
    assert round_price_to_tick(123.456, 0.01) == 123.45


def test_round_qty_to_step() -> None:
    assert round_qty_to_step(0.1234567, 0.00001) == 0.12345


def test_validate_min_notional() -> None:
    assert validate_min_notional(100.0, 0.1, 5.0)
    assert not validate_min_notional(10.0, 0.1, 5.0)


def test_validate_min_qty() -> None:
    assert validate_min_qty(0.2, 0.1)
    assert not validate_min_qty(0.01, 0.1)
