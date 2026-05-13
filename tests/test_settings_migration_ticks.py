import json

from app.core.config import SettingsStore


def test_legacy_tick_fields_are_migrated(tmp_path) -> None:
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"min_spread": 9, "entry_offset": 2, "exit_offset": 3, "target_capture": 4, "stop_loss": 5}))
    data = SettingsStore(str(p)).load()
    assert data.min_spread_ticks == 9
    assert data.entry_offset_ticks == 2
    assert data.exit_offset_ticks == 3
    assert not hasattr(data, "target_capture_ticks")
    assert data.stop_loss_ticks == 5
    assert data.stream_target_ticks == 28


def test_micro_grid_legacy_fields_migrate_on_load_and_not_persisted(tmp_path) -> None:
    p = tmp_path / "settings.json"
    p.write_text(
        json.dumps(
            {
                "micro_grid_max_active_buys": 7,
                "micro_grid_place_batch_size": 4,
                "micro_grid_place_interval_ms": 777,
                "micro_grid_max_inventory_u": 1234.0,
                "micro_grid_pause_buy_if_inventory_u_above": 987.0,
                "micro_grid_sell_first": False,
            }
        )
    )
    store = SettingsStore(str(p))
    data = store.load()
    assert data.stream_max_active_buys == 7
    assert data.stream_place_batch_size == 4
    assert data.stream_place_interval_ms == 777
    assert data.stream_sell_first is False

    saved = json.loads(p.read_text())
    assert "micro_grid_max_active_buys" not in saved
    assert "micro_grid_place_batch_size" not in saved
    assert "micro_grid_place_interval_ms" not in saved
    assert "micro_grid_max_inventory_u" not in saved
    assert "micro_grid_pause_buy_if_inventory_u_above" not in saved
    assert "micro_grid_sell_first" not in saved


def test_import_settings_json_accepts_legacy_micro_grid_fields(tmp_path) -> None:
    settings_path = tmp_path / "settings.json"
    store = SettingsStore(str(settings_path))
    store.save(store.load())

    import_path = tmp_path / "import.json"
    import_path.write_text(
        json.dumps(
            {
                "micro_grid_max_active_buys": 11,
                "micro_grid_place_batch_size": 2,
                "micro_grid_place_interval_ms": 610,
                "micro_grid_sell_first": False,
            }
        )
    )
    data = store.import_settings_json(str(import_path))
    assert data.stream_max_active_buys == 11
    assert data.stream_place_batch_size == 2
    assert data.stream_place_interval_ms == 610
    assert data.stream_sell_first is False


def test_export_settings_json_has_no_micro_grid_legacy_fields(tmp_path) -> None:
    p = tmp_path / "settings.json"
    store = SettingsStore(str(p))
    store.save(store.load())
    export_path = tmp_path / "export.json"
    store.export_settings_json(str(export_path))
    exported = json.loads(export_path.read_text())

    assert "micro_grid_max_active_buys" not in exported
    assert "micro_grid_place_batch_size" not in exported
    assert "micro_grid_place_interval_ms" not in exported
    assert "micro_grid_max_inventory_u" not in exported
    assert "micro_grid_pause_buy_if_inventory_u_above" not in exported
    assert "micro_grid_sell_first" not in exported
