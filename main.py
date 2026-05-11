from PySide6.QtWidgets import QApplication

from app.gui.main_window import MainWindow
from app.gui.micro_grid_window import MicroGridWindow
from app.gui.mode_selector import ModeSelectorDialog


def main() -> None:
    app = QApplication([])
    selector = ModeSelectorDialog()
    if not selector.exec():
        return

    if selector.selected_mode == ModeSelectorDialog.MICRO_GRID_MODE:
        window = MicroGridWindow()
    else:
        window = MainWindow()

    window.show()
    app.exec()


if __name__ == "__main__":
    main()
