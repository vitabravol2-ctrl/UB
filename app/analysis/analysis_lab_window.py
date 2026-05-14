from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QMainWindow, QPushButton, QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget

from app.analysis.config_generator import base_payload
from app.analysis.dry_runtime import DryTournamentRuntime


class AnalysisLabWindow(QMainWindow):
    def __init__(self, settings) -> None:
        super().__init__()
        self.setWindowTitle("Лаборатория анализа / Dry Tournament")
        self.resize(1250, 760)
        self.base = base_payload(settings)
        self.runtime: DryTournamentRuntime | None = None
        self.started_at = 0.0

        root = QWidget(); self.setCentralWidget(root)
        lay = QVBoxLayout(root)
        tools = QHBoxLayout()
        self.start_btn = QPushButton("ЗАПУСТИТЬ АНАЛИЗ"); self.start_btn.clicked.connect(self.start_analysis); tools.addWidget(self.start_btn)
        self.stop_btn = QPushButton("СТОП"); self.stop_btn.clicked.connect(self.stop_analysis); tools.addWidget(self.stop_btn)
        self.reset_btn = QPushButton("СБРОС"); self.reset_btn.clicked.connect(self.reset_analysis); tools.addWidget(self.reset_btn)
        self.reload_btn = QPushButton("ЗАГРУЗИТЬ ТЕКУЩИЕ НАСТРОЙКИ"); self.reload_btn.clicked.connect(self.reload_settings); tools.addWidget(self.reload_btn)
        lay.addLayout(tools)

        self.stats = QLabel("Активных профилей: 0 | Проверено: 0 | Скорость симуляции: 0/сек | Время работы: 0 сек | Лучший PnL: 0 | Средний PnL: 0")
        lay.addWidget(self.stats)

        content = QHBoxLayout()
        lay.addLayout(content)

        self.table = QTableWidget(0, 11)
        self.table.setHorizontalHeaderLabels(["Место", "PnL", "Winrate", "Циклы", "Победы", "Убытки", "Застревания", "Таймауты", "Рейтинг", "Тип профиля", "Профиль настроек"])
        content.addWidget(self.table, 3)

        self.best_panel = QFrame()
        self.best_panel.setFrameShape(QFrame.Shape.StyledPanel)
        best_lay = QVBoxLayout(self.best_panel)
        self.best_label = QLabel("Лучший профиль:\nнет данных")
        self.best_label.setTextFormat(Qt.TextFormat.PlainText)
        self.best_label.setWordWrap(True)
        best_lay.addWidget(self.best_label)
        content.addWidget(self.best_panel, 2)

        self.log = QTextEdit(); self.log.setReadOnly(True); self.log.setMaximumHeight(120); lay.addWidget(self.log)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.on_tick)

    def reload_settings(self) -> None:
        self.log.append("Загружен снимок текущих настроек для анализа")

    def start_analysis(self) -> None:
        if self.runtime is None:
            self.runtime = DryTournamentRuntime(self.base)
        self.started_at = time.monotonic()
        self.timer.start(250)
        self.log.append("Анализ запущен")

    def stop_analysis(self) -> None:
        self.timer.stop()
        self.log.append("Анализ остановлен")

    def reset_analysis(self) -> None:
        self.timer.stop()
        self.runtime = DryTournamentRuntime(self.base)
        self.table.setRowCount(0)
        self.log.append("Анализ сброшен")

    def _profile_kind(self, row: dict) -> str:
        p = row["params"]
        target_small = p["stream_target_ticks"] <= 28
        timeout_small = p["stream_sell_timeout_ms"] <= 1800
        active_high = p["stream_max_active_buys"] >= 8
        if target_small and timeout_small and active_high:
            return "АГРЕССИВНЫЙ"
        target_high = p["stream_target_ticks"] >= 42
        timeout_high = p["stream_sell_timeout_ms"] >= 2600
        active_low = p["stream_max_active_buys"] <= 4
        if target_high and timeout_high and active_low:
            return "ОСТОРОЖНЫЙ"
        return "БАЛАНС"

    def _row_color(self, row: dict) -> QColor:
        if row["pnl"] < 0 or row["losses"] > row["wins"] or row["exit_stuck"] >= 4:
            return QColor("#ffd7d7")
        if row["pnl"] > 0 and row["winrate"] >= 60.0 and row["losses"] <= max(1, row["wins"] // 2):
            return QColor("#d9f7d9")
        return QColor("#fff7cc")

    def _format_summary(self, p: dict) -> str:
        return (
            f"Цель: {p['stream_target_ticks']} тиков | Мин.профит: {p['stream_min_profit_ticks']} | "
            f"SELL timeout: {p['stream_sell_timeout_ms']}мс | Retry: {p['stream_sell_retry_max']}x{p['stream_sell_retry_step_ticks']} | "
            f"BUY fast: {p['buy_timeout_ms_fast']}мс | Активных BUY: {p['stream_max_active_buys']} | Recycle: {p['stream_recycle_delay_ms']}мс"
        )

    def _update_best_panel(self, row: dict) -> None:
        p = row["params"]
        self.best_label.setText(
            "Лучший профиль:\n"
            f"- Рейтинг: {row['score']:.2f}\n"
            f"- PnL: {row['pnl']:.2f}\n"
            f"- Winrate: {row['winrate']:.1f}%\n"
            f"- Циклы: {row['cycles']}\n"
            f"- Победы / Убытки: {row['wins']} / {row['losses']}\n"
            f"- Застревания: {row['exit_stuck']}\n"
            f"- Таймауты: {row['timeouts']}\n"
            "\n"
            "Настройки:\n"
            f"- stream_target_ticks: {p['stream_target_ticks']}\n"
            f"- stream_min_profit_ticks: {p['stream_min_profit_ticks']}\n"
            f"- stream_sell_timeout_ms: {p['stream_sell_timeout_ms']}\n"
            f"- stream_sell_retry_max: {p['stream_sell_retry_max']}\n"
            f"- stream_sell_retry_step_ticks: {p['stream_sell_retry_step_ticks']}\n"
            f"- buy_timeout_ms_fast: {p['buy_timeout_ms_fast']}\n"
            f"- min_spread_ticks: {p['min_spread_ticks']}\n"
            f"- entry_chase_ticks: {p['entry_chase_ticks']}\n"
            f"- stream_max_active_buys: {p['stream_max_active_buys']}\n"
            f"- stream_recycle_delay_ms: {p['stream_recycle_delay_ms']}\n"
            f"\nТип профиля: {self._profile_kind(row)}"
        )

    def on_tick(self) -> None:
        if self.runtime is None:
            return
        self.runtime.tick(budget=80)
        rows = self.runtime.top_rows(100)
        self.table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            wr = f"{row['winrate']:.1f}%"
            summary = self._format_summary(row["params"])
            vals = [str(i + 1), f"{row['pnl']:.2f}", wr, str(row['cycles']), str(row['wins']), str(row['losses']), str(row['exit_stuck']), str(row['timeouts']), f"{row['score']:.2f}", self._profile_kind(row), summary]
            color = self._row_color(row)
            for col, v in enumerate(vals):
                item = QTableWidgetItem(v)
                item.setBackground(color)
                self.table.setItem(i, col, item)
        if rows:
            self._update_best_panel(rows[0])
        for event in self.runtime.pull_events():
            self.log.append(event)
        st = self.runtime.stats()
        self.stats.setText(
            f"Активных профилей: {st['active']} | Проверено: {st['tested']} | Скорость симуляции: {st['sim_speed']:.1f}/сек | "
            f"Время работы: {st['runtime_s']:.1f} сек | Лучший PnL: {st['best_pnl']:.2f} | Средний PnL: {st['avg_pnl']:.2f} | "
            f"Лучший Winrate: {st['best_winrate']:.1f}% | Циклов/час: {st['cycles_per_hour']:.0f}"
        )
