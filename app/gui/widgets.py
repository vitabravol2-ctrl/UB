from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QGroupBox, QLabel, QSizePolicy, QVBoxLayout, QWidget


def build_kv_card(title: str, rows: list[tuple[str, str]], *, compact: bool = False, label_width: int = 138, columns: int = 1) -> tuple[QGroupBox, dict[str, QLabel]]:
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
    for col in range(columns):
        layout.setColumnStretch(col * 2, 0)
        layout.setColumnStretch(col * 2 + 1, 1)

    refs: dict[str, QLabel] = {}
    total_rows = (len(rows) + columns - 1) // columns
    for idx, (k, v) in enumerate(rows):
        row = idx % total_rows
        col = idx // total_rows
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

        layout.addWidget(key_label, row, col * 2)
        layout.addWidget(val_label, row, col * 2 + 1)

    wrapper.addWidget(content)
    box.setLayout(wrapper)
    min_height = 46 + total_rows * row_height + max(0, total_rows - 1) * 2
    box.setMinimumHeight(min_height)
    box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
    return box, refs


def kv_card(title: str, rows: list[tuple[str, str]], *, compact: bool = False) -> tuple[QGroupBox, dict[str, QLabel]]:
    return build_kv_card(title, rows, compact=compact)


def big_value(title: str, value: str, *, compact: bool = False) -> tuple[QGroupBox, QLabel]:
    box = QGroupBox(title)
    lay = QVBoxLayout()
    lay.setContentsMargins(10, 8, 10, 8) if compact else lay.setContentsMargins(12, 14, 12, 12)
    val = QLabel(value)
    val.setAlignment(Qt.AlignCenter)
    font_size = 24 if compact else 40
    min_height = 52 if compact else 122
    box_min = 126 if compact else 180
    val.setStyleSheet(f"font-size: {font_size}px; font-weight: 900; font-family: 'JetBrains Mono','Consolas','Segoe UI';")
    val.setMinimumHeight(min_height)
    lay.addWidget(val)
    box.setLayout(lay)
    box.setMinimumHeight(box_min)
    box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
    return box, val


BADGE_STYLES = {
    "ok": "background:#1f5f3a;color:#d8ffe9;border:1px solid #2f9a5d;",
    "error": "background:#5d1f24;color:#ffd9dc;border:1px solid #a43a45;",
    "warn": "background:#6a4b1e;color:#ffe9c6;border:1px solid #b8832a;",
    "info": "background:#1e3f6a;color:#d8e9ff;border:1px solid #2d69aa;",
    "neutral": "background:#2a313d;color:#dde5f1;border:1px solid #435064;",
}


def build_status_badge(text: str, state: str) -> QLabel:
    badge = QLabel(text)
    badge.setAlignment(Qt.AlignCenter)
    badge.setMinimumHeight(24)
    update_badge(badge, state, text)
    return badge


def update_badge(widget: QLabel, state: str, text: str) -> None:
    style = BADGE_STYLES.get(state, BADGE_STYLES["neutral"])
    widget.setText(text)
    widget.setStyleSheet(f"padding:2px 8px;border-radius:10px;font-weight:700;{style}")


def build_terminal_card(title: str, rows: list[tuple[str, str]]) -> tuple[QGroupBox, dict[str, QLabel]]:
    return build_kv_card(title, rows, compact=True)
