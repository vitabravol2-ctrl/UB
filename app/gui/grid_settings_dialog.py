from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QVBoxLayout

from app.core.grid_config import GRID_SETTINGS_STORE, GridSettings


class GridSettingsDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Micro Grid Settings")
        self.setMinimumWidth(420)

        self.upper_price = QLineEdit()
        self.lower_price = QLineEdit()
        self.budget_u = QLineEdit()
        self.levels = QLineEdit()
        self.profit_ticks = QLineEdit()
        self.max_exposure_u = QLineEdit()
        self.auto_float_enabled = QCheckBox()
        self.live_enabled = QCheckBox()

        form = QFormLayout()
        form.addRow("Upper Price", self.upper_price)
        form.addRow("Lower Price", self.lower_price)
        form.addRow("Budget U", self.budget_u)
        form.addRow("Levels", self.levels)
        form.addRow("Profit Ticks", self.profit_ticks)
        form.addRow("Max Exposure U", self.max_exposure_u)
        form.addRow("Auto Float enabled", self.auto_float_enabled)
        form.addRow("LIVE Small enabled", self.live_enabled)

        buttons = QHBoxLayout()
        self.save_btn = QPushButton("Save")
        self.defaults_btn = QPushButton("Load Defaults")
        self.apply_btn = QPushButton("Apply")
        self.cancel_btn = QPushButton("Cancel")
        buttons.addWidget(self.save_btn)
        buttons.addWidget(self.defaults_btn)
        buttons.addWidget(self.apply_btn)
        buttons.addWidget(self.cancel_btn)

        root = QVBoxLayout(self)
        root.addLayout(form)
        root.addLayout(buttons)

        self.save_btn.clicked.connect(self.save)
        self.defaults_btn.clicked.connect(self.load_defaults)
        self.apply_btn.clicked.connect(self.accept)
        self.cancel_btn.clicked.connect(self.reject)
        self.live_enabled.toggled.connect(self._warn_live)
        self.apply_settings(GRID_SETTINGS_STORE.load())

    def _warn_live(self, checked: bool) -> None:
        if checked:
            QMessageBox.warning(self, "LIVE warning", "LIVE mode places real orders. Start with small budget.")

    def collect_settings(self) -> GridSettings:
        return GridSettings(
            upper_price=float(self.upper_price.text()),
            lower_price=float(self.lower_price.text()),
            budget_u=float(self.budget_u.text()),
            levels=int(self.levels.text()),
            profit_ticks=int(self.profit_ticks.text()),
            max_exposure_u=float(self.max_exposure_u.text()),
            auto_float_enabled=self.auto_float_enabled.isChecked(),
            live_enabled=self.live_enabled.isChecked(),
        )

    def apply_settings(self, s: GridSettings) -> None:
        self.upper_price.setText(str(s.upper_price))
        self.lower_price.setText(str(s.lower_price))
        self.budget_u.setText(str(s.budget_u))
        self.levels.setText(str(s.levels))
        self.profit_ticks.setText(str(s.profit_ticks))
        self.max_exposure_u.setText(str(s.max_exposure_u))
        self.auto_float_enabled.setChecked(s.auto_float_enabled)
        self.live_enabled.setChecked(s.live_enabled)

    def save(self) -> None:
        GRID_SETTINGS_STORE.save(self.collect_settings())

    def load_defaults(self) -> None:
        self.apply_settings(GridSettings())
