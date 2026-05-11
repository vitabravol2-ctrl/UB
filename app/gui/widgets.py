from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QGroupBox, QLabel, QSizePolicy, QVBoxLayout, QWidget


def kv_card(title: str, rows: list[tuple[str, str]], *, compact: bool = False) -> tuple[QGroupBox, dict[str, QLabel]]:
    box = QGroupBox(title)
    wrapper = QVBoxLayout()
    wrapper.setContentsMargins(8, 8, 8, 8)

    content = QWidget()
    layout = QGridLayout(content)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setHorizontalSpacing(12)
    layout.setVerticalSpacing(4 if compact else 6)
    layout.setColumnStretch(0, 0)
    layout.setColumnStretch(1, 1)

    refs: dict[str, QLabel] = {}
    for row, (k, v) in enumerate(rows):
        key_label = QLabel(k)
        key_label.setProperty("role", "secondary")
        key_label.setMinimumHeight(22)
        key_label.setMinimumWidth(140)
        key_label.setMaximumWidth(170)
        key_label.setWordWrap(False)

        val_label = QLabel(v)
        val_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        val_label.setMinimumHeight(22)
        val_label.setWordWrap(False)
        val_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        refs[k] = val_label

        layout.addWidget(key_label, row, 0)
        layout.addWidget(val_label, row, 1)

    wrapper.addWidget(content)
    box.setLayout(wrapper)
    box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
    return box, refs


def big_value(title: str, value: str) -> tuple[QGroupBox, QLabel]:
    box = QGroupBox(title)
    lay = QVBoxLayout()
    lay.setContentsMargins(12, 14, 12, 12)
    val = QLabel(value)
    val.setAlignment(Qt.AlignCenter)
    val.setStyleSheet("font-size: 40px; font-weight: 900; font-family: 'JetBrains Mono','Consolas','Segoe UI';")
    val.setMinimumHeight(96)
    lay.addWidget(val)
    box.setLayout(lay)
    return box, val
