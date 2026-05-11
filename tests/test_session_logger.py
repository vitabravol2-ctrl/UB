from app.core.session_logger import SessionLogger


def test_session_log_file_created(tmp_path) -> None:
    logger = SessionLogger(base_dir=str(tmp_path), symbol="BTCU")
    logger.log("GRID", "started")
    assert logger.path.exists()


def test_gui_log_cap() -> None:
    logger = SessionLogger(symbol="BTCU")
    for i in range(450):
        logger.append_gui_line(f"line-{i}")
    assert len(logger.get_gui_lines()) == 300
