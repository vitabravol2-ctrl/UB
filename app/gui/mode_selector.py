from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout


class ModeSelectorDialog(QDialog):
    CONVEYOR_MODE = "conveyor"
    MICRO_GRID_MODE = "micro_grid"

    def __init__(self) -> None:
        super().__init__()
        self.selected_mode = self.CONVEYOR_MODE
        self.setWindowTitle("UB v0.8.0 / Mode Selector")
        self.resize(420, 180)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Выберите режим запуска:"))

        buttons = QHBoxLayout()
        conveyor_btn = QPushButton("Conveyor Mode")
        micro_grid_btn = QPushButton("Micro Grid Mode")
        conveyor_btn.clicked.connect(self._select_conveyor)
        micro_grid_btn.clicked.connect(self._select_micro_grid)
        buttons.addWidget(conveyor_btn)
        buttons.addWidget(micro_grid_btn)
        layout.addLayout(buttons)

    def _select_conveyor(self) -> None:
        self.selected_mode = self.CONVEYOR_MODE
        self.accept()

    def _select_micro_grid(self) -> None:
        self.selected_mode = self.MICRO_GRID_MODE
        self.accept()
