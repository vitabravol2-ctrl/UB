import json

from app.core.config import SettingsStore


def test_legacy_tick_fields_are_migrated(tmp_path) -> None:
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"min_spread": 9, "entry_offset": 2, "exit_offset": 3, "target_capture": 4, "stop_loss": 5}))
    data = SettingsStore(str(p)).load()
    assert data.min_spread_ticks == 9
    assert data.entry_offset_ticks == 2
    assert data.exit_offset_ticks == 3
    assert data.target_capture_ticks == 4
    assert data.stop_loss_ticks == 5
    assert data.stream_target_ticks == 80
