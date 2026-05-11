PALETTE = {
    "background": "#0B0F14",
    "cards": "#111827",
    "border": "#263241",
    "text": "#E5E7EB",
    "secondary": "#9CA3AF",
    "green": "#22C55E",
    "yellow": "#FACC15",
    "red": "#EF4444",
    "blue": "#3B82F6",
    "purple": "#A855F7",
}


def main_qss() -> str:
    p = PALETTE
    return f"""
    QWidget {{ background-color: {p['background']}; color: {p['text']}; font-size: 13px; }}
    QGroupBox {{ background-color: {p['cards']}; border: 1px solid {p['border']}; border-radius: 10px; margin-top: 10px; font-weight: 600; }}
    QGroupBox::title {{ left: 10px; top: -6px; padding: 0 4px; color: {p['secondary']}; }}
    QLabel[role='secondary'] {{ color: {p['secondary']}; }}
    QPushButton {{ min-height: 38px; border-radius: 9px; border: 1px solid {p['border']}; padding: 0 12px; font-weight: 700; background: #1F2937; }}
    QPushButton:hover {{ border: 1px solid {p['blue']}; }}
    QPushButton:disabled {{ background: #374151; color: #9CA3AF; }}
    QPushButton[kind='primary'] {{ background: {p['green']}; color: #03180b; }}
    QPushButton[kind='warning'] {{ background: {p['yellow']}; color: #3b2f00; }}
    QPushButton[kind='danger'] {{ background: {p['red']}; color: white; }}
    """
