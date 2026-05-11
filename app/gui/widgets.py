from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFormLayout, QGroupBox, QLabel, QSizePolicy, QVBoxLayout


def kv_card(title: str, rows: list[tuple[str, str]], *, compact: bool = False) -> tuple[QGroupBox, dict[str, QLabel]]:
    box = QGroupBox(title)
    layout = QFormLayout()
    layout.setRowWrapPolicy(QFormLayout.DontWrapRows)
    layout.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
    layout.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    layout.setFormAlignment(Qt.AlignTop | Qt.AlignLeft)
    layout.setHorizontalSpacing(14)
    layout.setVerticalSpacing(8 if compact else 6)
    layout.setContentsMargins(12, 14, 12, 12)
    refs: dict[str, QLabel] = {}
    for k, v in rows:
        key_label = QLabel(k)
        key_label.setProperty("role", "secondary")
        key_label.setMinimumHeight(24)
        key_label.setMinimumWidth(150 if compact else 140)
        key_label.setMaximumWidth(180 if compact else 170)
        key_label.setWordWrap(False)
        val_label = QLabel(v)
        val_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        val_label.setMinimumHeight(24)
        val_label.setWordWrap(False)
        val_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        refs[k] = val_label
        layout.addRow(key_label, val_label)
    box.setLayout(layout)
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
