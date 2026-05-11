from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFormLayout, QGroupBox, QLabel, QVBoxLayout


def kv_card(title: str, rows: list[tuple[str, str]]) -> tuple[QGroupBox, dict[str, QLabel]]:
    box = QGroupBox(title)
    layout = QFormLayout()
    layout.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    layout.setFormAlignment(Qt.AlignTop | Qt.AlignLeft)
    layout.setHorizontalSpacing(16)
    layout.setVerticalSpacing(6)
    layout.setContentsMargins(10, 14, 10, 10)
    refs: dict[str, QLabel] = {}
    for k, v in rows:
        key_label = QLabel(k)
        key_label.setProperty("role", "secondary")
        key_label.setMinimumHeight(22)
        val_label = QLabel(v)
        val_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        val_label.setMinimumHeight(22)
        refs[k] = val_label
        layout.addRow(key_label, val_label)
    box.setLayout(layout)
    return box, refs


def big_value(title: str, value: str) -> tuple[QGroupBox, QLabel]:
    box = QGroupBox(title)
    lay = QVBoxLayout()
    val = QLabel(value)
    val.setAlignment(Qt.AlignCenter)
    val.setStyleSheet("font-size: 42px; font-weight: 900; font-family: 'JetBrains Mono','Consolas','Segoe UI';")
    lay.addWidget(val)
    box.setLayout(lay)
    return box, val
