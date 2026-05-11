from PySide6.QtWidgets import QFormLayout, QGroupBox, QLabel, QVBoxLayout


def kv_card(title: str, rows: list[tuple[str, str]]) -> tuple[QGroupBox, dict[str, QLabel]]:
    box = QGroupBox(title)
    layout = QFormLayout()
    refs: dict[str, QLabel] = {}
    for k, v in rows:
        key_label = QLabel(k)
        key_label.setProperty("role", "secondary")
        val_label = QLabel(v)
        refs[k] = val_label
        layout.addRow(key_label, val_label)
    box.setLayout(layout)
    return box, refs


def big_value(title: str, value: str) -> tuple[QGroupBox, QLabel]:
    box = QGroupBox(title)
    lay = QVBoxLayout()
    val = QLabel(value)
    val.setStyleSheet("font-size: 28px; font-weight: 800;")
    lay.addWidget(val)
    box.setLayout(lay)
    return box, val
