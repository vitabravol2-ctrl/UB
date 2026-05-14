from __future__ import annotations

import time

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QMainWindow, QPushButton, QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget

from app.analysis.config_generator import base_payload
from app.analysis.dry_runtime import DryTournamentRuntime


class AnalysisLabWindow(QMainWindow):
    def __init__(self, settings) -> None:
        super().__init__()
        self.setWindowTitle("Analysis Lab / Dry Tournament")
        self.resize(1250, 760)
        self.base = base_payload(settings)
        self.runtime: DryTournamentRuntime | None = None
        self.started_at = 0.0

        root = QWidget(); self.setCentralWidget(root)
        lay = QVBoxLayout(root)
        tools = QHBoxLayout()
        self.start_btn = QPushButton("START ANALYSIS"); self.start_btn.clicked.connect(self.start_analysis); tools.addWidget(self.start_btn)
        self.stop_btn = QPushButton("STOP"); self.stop_btn.clicked.connect(self.stop_analysis); tools.addWidget(self.stop_btn)
        self.reset_btn = QPushButton("RESET"); self.reset_btn.clicked.connect(self.reset_analysis); tools.addWidget(self.reset_btn)
        self.reload_btn = QPushButton("LOAD CURRENT SETTINGS"); self.reload_btn.clicked.connect(self.reload_settings); tools.addWidget(self.reload_btn)
        lay.addLayout(tools)

        self.stats = QLabel("Active: 0 | Tested: 0 | Sim speed: 0/s | Runtime: 0s | Best pnl: 0 | Avg pnl: 0")
        lay.addWidget(self.stats)

        self.table = QTableWidget(0, 10)
        self.table.setHorizontalHeaderLabels(["Rank", "PnL", "Winrate", "Cycles", "Wins", "Losses", "ExitStuck", "Timeouts", "Score", "ConfigSummary"])
        lay.addWidget(self.table)
        self.log = QTextEdit(); self.log.setReadOnly(True); self.log.setMaximumHeight(120); lay.addWidget(self.log)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.on_tick)

    def reload_settings(self) -> None:
        self.log.append("Settings snapshot loaded for analysis")

    def start_analysis(self) -> None:
        if self.runtime is None:
            self.runtime = DryTournamentRuntime(self.base)
        self.started_at = time.monotonic()
        self.timer.start(250)
        self.log.append("Analysis started (dry only)")

    def stop_analysis(self) -> None:
        self.timer.stop()
        self.log.append("Analysis stopped")

    def reset_analysis(self) -> None:
        self.timer.stop()
        self.runtime = DryTournamentRuntime(self.base)
        self.table.setRowCount(0)
        self.log.append("Analysis reset")

    def on_tick(self) -> None:
        if self.runtime is None:
            return
        self.runtime.tick(budget=80)
        rows = self.runtime.top_rows(100)
        self.table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            wr = f"{row['winrate']:.1f}%"
            summary = (
                f"tt={row['params']['stream_target_ticks']} mp={row['params']['stream_min_profit_ticks']} "
                f"sto={row['params']['stream_sell_timeout_ms']} rt={row['params']['stream_sell_retry_max']} "
                f"rch={row['params']['stream_sell_retry_step_ticks']} fast={row['params']['buy_timeout_ms_fast']}"
            )
            vals = [str(i + 1), f"{row['pnl']:.2f}", wr, str(row['cycles']), str(row['wins']), str(row['losses']), str(row['exit_stuck']), str(row['timeouts']), f"{row['score']:.2f}", summary]
            for col, v in enumerate(vals):
                self.table.setItem(i, col, QTableWidgetItem(v))
        st = self.runtime.stats()
        self.stats.setText(
            f"Active configs: {st['active']} | Tested: {st['tested']} | Sim speed: {st['sim_speed']:.1f}/s | "
            f"Runtime: {st['runtime_s']:.1f}s | Best pnl: {st['best_pnl']:.2f} | Avg pnl: {st['avg_pnl']:.2f} | "
            f"Best winrate: {st['best_winrate']:.1f}% | Cycles/hour: {st['cycles_per_hour']:.0f}"
        )
