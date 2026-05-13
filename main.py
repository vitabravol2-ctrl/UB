from PySide6.QtWidgets import QApplication

from app.gui.main_window import MainWindow
from app.gui.micro_grid_window import MicroGridWindow
from app.gui.mode_selector import ModeSelectorDialog


ENABLE_MICRO_GRID_MODE = False


def main() -> None:
    app = QApplication([])

    if ENABLE_MICRO_GRID_MODE:
        selector = ModeSelectorDialog()
        if selector.exec() != selector.DialogCode.Accepted:
            return
        if selector.selected_mode == ModeSelectorDialog.MICRO_GRID_MODE:
            window = MicroGridWindow()
        else:
            window = MainWindow()
    else:
        window = MainWindow()

    window.show()
    app.exec()


if __name__ == "__main__":
    main()
