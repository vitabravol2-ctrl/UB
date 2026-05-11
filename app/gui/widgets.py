from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QGroupBox, QLabel, QSizePolicy, QVBoxLayout, QWidget


def build_kv_card(title: str, rows: list[tuple[str, str]], *, compact: bool = False, label_width: int = 138) -> tuple[QGroupBox, dict[str, QLabel]]:
    box = QGroupBox(title)
    wrapper = QVBoxLayout()
    wrapper.setContentsMargins(10, 8, 10, 8)
    wrapper.setSpacing(0)

    content = QWidget()
    layout = QGridLayout(content)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setHorizontalSpacing(10)
    row_height = 26
    layout.setVerticalSpacing(2)
    layout.setColumnStretch(0, 0)
    layout.setColumnStretch(1, 1)

    refs: dict[str, QLabel] = {}
    for row, (k, v) in enumerate(rows):
        key_label = QLabel(k)
        key_label.setProperty("role", "secondary")
        key_label.setMinimumHeight(row_height)
        key_label.setFixedWidth(label_width)
        key_label.setWordWrap(False)
        key_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        val_label = QLabel(v)
        val_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        val_label.setMinimumHeight(row_height)
        val_label.setWordWrap(False)
        val_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        val_label.setTextInteractionFlags(Qt.NoTextInteraction)
        refs[k] = val_label

        layout.addWidget(key_label, row, 0)
        layout.addWidget(val_label, row, 1)

    wrapper.addWidget(content)
    box.setLayout(wrapper)
    min_height = 46 + len(rows) * row_height + max(0, len(rows) - 1) * 2
    box.setMinimumHeight(min_height)
    box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
    return box, refs


def kv_card(title: str, rows: list[tuple[str, str]], *, compact: bool = False) -> tuple[QGroupBox, dict[str, QLabel]]:
    return build_kv_card(title, rows, compact=compact)


def big_value(title: str, value: str) -> tuple[QGroupBox, QLabel]:
    box = QGroupBox(title)
    lay = QVBoxLayout()
    lay.setContentsMargins(12, 14, 12, 12)
    val = QLabel(value)
    val.setAlignment(Qt.AlignCenter)
    val.setStyleSheet("font-size: 40px; font-weight: 900; font-family: 'JetBrains Mono','Consolas','Segoe UI';")
    val.setMinimumHeight(122)
    lay.addWidget(val)
    box.setLayout(lay)
    box.setMinimumHeight(180)
    box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
    return box, val
