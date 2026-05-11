from pathlib import Path

from app.core.crash_logger import GridCrashLogger
from app.core.session_logger import SessionLogger


def test_session_log_created_on_open(tmp_path: Path) -> None:
    logger = SessionLogger(symbol="BTCU", base_dir=str(tmp_path))
    logger.log("GRID", "terminal opened")
    assert logger.path.exists()


def test_exception_written_to_crash_log(tmp_path: Path) -> None:
    crash = GridCrashLogger(base_dir=str(tmp_path))
    crash.last_gui_action = "start_live"
    try:
        raise RuntimeError("boom")
    except RuntimeError as exc:
        crash._handle_exception(type(exc), exc, exc.__traceback__)
    files = list(tmp_path.glob("grid_crash_*.log"))
    assert files
    assert "RuntimeError" in files[0].read_text(encoding="utf-8")
