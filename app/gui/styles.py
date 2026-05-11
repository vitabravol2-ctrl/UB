PALETTE = {
    "background": "#0B0F14",
    "cards": "#111827",
    "border": "#263241",
    "text": "#E5E7EB",
    "secondary": "#9CA3AF",
    "green": "#22C55E",
    "green_hot": "#4DFF7A",
    "yellow": "#FACC15",
    "red": "#EF4444",
    "blue": "#3B82F6",
}


def main_qss() -> str:
    p = PALETTE
    return f"""
    QWidget {{ background-color: {p['background']}; color: {p['text']}; font-size: 13px; font-family: 'Segoe UI'; }}
    QLabel#topStatus {{ font-size: 16px; font-weight: 700; padding: 6px 2px; }}
    QGroupBox {{ background-color: {p['cards']}; border: 1px solid {p['border']}; border-radius: 10px; margin-top: 10px; font-weight: 700; }}
    QGroupBox::title {{ left: 10px; top: -6px; padding: 0 4px; color: {p['secondary']}; }}
    QLabel[role='secondary'] {{ color: {p['secondary']}; }}

    QPushButton {{ min-height: 42px; border-radius: 10px; border: 1px solid {p['border']}; padding: 0 14px; font-weight: 800; background: #1F2937; }}
    QPushButton:hover {{ border: 1px solid {p['blue']}; }}
    QPushButton:disabled {{ background: #374151; color: #9CA3AF; border-color:#4B5563; }}
    QPushButton[kind='start'] {{ background: {p['green']}; color: #03180b; }}
    QPushButton[kind='stop'] {{ background: {p['red']}; color: white; }}
    QPushButton[kind='danger'] {{ background: {p['red']}; color: white; }}
    QPushButton[kind='neutral'] {{ background: #334155; color: #DBEAFE; }}

    QGroupBox[state='bad'] {{ border: 1px solid #6B7280; }}
    QGroupBox[state='watch'] {{ border: 1px solid {p['yellow']}; }}
    QGroupBox[state='ready'] {{ border: 1px solid {p['green']}; }}
    QGroupBox[state='hot'] {{ border: 2px solid {p['green_hot']}; }}
    QGroupBox[state='api-ok'] {{ border: 1px solid {p['green']}; }}
    QGroupBox[state='api-error'] {{ border: 1px solid {p['red']}; }}
    QGroupBox[state='api-notset'] {{ border: 1px solid #6B7280; }}
    QGroupBox[state='safe'] {{ border: 1px solid {p['green']}; }}
    QGroupBox[state='warning'] {{ border: 1px solid {p['yellow']}; }}
    QGroupBox[state='danger'] {{ border: 1px solid {p['red']}; }}
    """
