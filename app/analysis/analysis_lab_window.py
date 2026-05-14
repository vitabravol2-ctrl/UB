from __future__ import annotations

import time
from dataclasses import asdict, replace
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QMainWindow, QPushButton, QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget

from app.analysis.config_generator import MUTABLE_PARAMS, base_payload
from app.analysis.dry_runtime import DryTournamentRuntime
from app.core.config import SETTINGS_STORE
from PySide6.QtWidgets import QMessageBox


ALLOWED_APPLY_FIELDS = tuple(MUTABLE_PARAMS.keys()) + (
    "stream_target_ticks",
    "stream_min_profit_ticks",
    "stream_sell_timeout_ms",
    "stream_sell_retry_max",
    "stream_sell_retry_step_ticks",
    "buy_timeout_ms_fast",
    "min_spread_ticks",
    "entry_chase_ticks",
    "stream_max_active_buys",
    "stream_recycle_delay_ms",
)


class AnalysisLabWindow(QMainWindow):
    def __init__(self, settings) -> None:
        super().__init__()
        self.setWindowTitle("Лаборатория анализа / Dry Tournament")
        self.resize(1250, 760)
        self.base = base_payload(settings)
        self.settings = settings
        self.runtime: DryTournamentRuntime | None = None
        self.started_at = 0.0
        self.selected_profile: dict | None = None
        self.baseline_profile = self._build_baseline_from_settings()
        self.applied_baseline: dict | None = None
        self.last_notified_leader_id: int | None = None

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

        self.table = QTableWidget(0, 18)
        self.table.setHorizontalHeaderLabels(["Место", "PnL", "Winrate", "Циклы", "Победы", "Убытки", "Застревания", "Таймауты", "Рейтинг", "Класс", "Турнир", "Tier", "Деградация", "Поколение", "Lineage", "Stability", "Mutation", "Профиль настроек"])
        content.addWidget(self.table, 3)
        self.table.itemSelectionChanged.connect(self._on_table_selection)

        self.best_panel = QFrame()
        self.best_panel.setFrameShape(QFrame.Shape.StyledPanel)
        best_lay = QVBoxLayout(self.best_panel)
        self.best_label = QLabel("Текущий лидер:\nнет данных")
        self.best_label.setTextFormat(Qt.TextFormat.PlainText)
        self.best_label.setWordWrap(True)
        best_lay.addWidget(self.best_label)

        self.baseline_label = QLabel("Текущий / базовый профиль:\nнет данных")
        self.baseline_label.setTextFormat(Qt.TextFormat.PlainText)
        self.baseline_label.setWordWrap(True)
        best_lay.addWidget(self.baseline_label)

        self.notice_label = QLabel("")
        self.notice_label.setTextFormat(Qt.TextFormat.PlainText)
        self.notice_label.setWordWrap(True)
        best_lay.addWidget(self.notice_label)

        actions = QGridLayout()
        self.select_btn = QPushButton("ВЫБРАТЬ ПРОФИЛЬ"); self.select_btn.clicked.connect(self.select_profile_from_table); actions.addWidget(self.select_btn, 0, 0)
        self.apply_btn = QPushButton("ПРИМЕНИТЬ В SETTINGS"); self.apply_btn.clicked.connect(self.apply_selected_profile); actions.addWidget(self.apply_btn, 0, 1)
        self.apply_leader_btn = QPushButton("ПРИМЕНИТЬ ЛИДЕРА"); self.apply_leader_btn.clicked.connect(self.apply_leader_profile); actions.addWidget(self.apply_leader_btn, 1, 1)
        self.pick_leader_btn = QPushButton("ВЫБРАТЬ ЛИДЕРА"); self.pick_leader_btn.clicked.connect(self.select_leader_profile); actions.addWidget(self.pick_leader_btn, 1, 0)
        best_lay.addLayout(actions)
        content.addWidget(self.best_panel, 2)

        self.log = QTextEdit(); self.log.setReadOnly(True); self.log.setMaximumHeight(120); lay.addWidget(self.log)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.on_tick)
        self._update_baseline_panel()

    def _build_baseline_from_settings(self) -> dict:
        payload = {k: getattr(self.settings, k) for k in ALLOWED_APPLY_FIELDS}
        return {"id": 0, "score": 0.0, "pnl": 0.0, "winrate": 0.0, "cycles": 0, "wins": 0, "losses": 0, "exit_stuck": 0, "timeouts": 0, "params": payload, "source": "current settings"}

    def _on_table_selection(self) -> None:
        row = self.table.currentRow()
        if self.runtime is None or row < 0:
            return
        rows = self.runtime.top_rows(100)
        if row < len(rows):
            self.selected_profile = rows[row]

    def reload_settings(self) -> None:
        src = Path(SETTINGS_STORE.path)
        loaded = SETTINGS_STORE.load()
        self.settings = loaded
        self.base = base_payload(loaded)
        self.baseline_profile = self._build_baseline_from_settings()
        self.applied_baseline = None
        self._update_baseline_panel()

        backup = src.with_name("settings.backup.analysis.json")
        diff_lines: list[str] = []
        if backup.exists():
            try:
                import json
                cur = json.loads(src.read_text(encoding="utf-8"))
                prev = json.loads(backup.read_text(encoding="utf-8"))
                for k in sorted(ALLOWED_APPLY_FIELDS):
                    if cur.get(k) != prev.get(k):
                        diff_lines.append(f"- {k}: {prev.get(k)} -> {cur.get(k)}")
            except Exception:
                diff_lines.append("- backup сравнение недоступно")
        else:
            diff_lines.append("- backup не найден")

        params_in_analysis = ", ".join(sorted(MUTABLE_PARAMS.keys()))
        fixed_params = "order_size_u, live_enabled, api/ws/balances"
        p = self.baseline_profile["params"]
        self.log.append(
            "settings.json успешно загружен\n"
            f"Путь файла: {src}\n"
            f"Время загрузки: {time.strftime('%Y-%m-%d %H:%M:%S')} UTC\n"
            "Загружен базовый профиль:\n"
            f"target={p.get('stream_target_ticks')}\nmin_profit={p.get('stream_min_profit_ticks')}\n"
            f"sell_timeout={p.get('stream_sell_timeout_ms')}\nretry={p.get('stream_sell_retry_max')}x{p.get('stream_sell_retry_step_ticks')}\n"
            f"buy_fast={p.get('buy_timeout_ms_fast')}\nactive_buys={p.get('stream_max_active_buys')}\nrecycle={p.get('stream_recycle_delay_ms')}\n"
            f"Параметры в анализе: {params_in_analysis}\n"
            f"Зафиксированные параметры: {fixed_params}\n"
            "Отличия от backup:\n" + "\n".join(diff_lines)
        )

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
            return "FAST_SCALPER"
        target_high = p["stream_target_ticks"] >= 42
        timeout_high = p["stream_sell_timeout_ms"] >= 2600
        active_low = p["stream_max_active_buys"] <= 4
        if target_high and timeout_high and active_low:
            return "LOW_RISK"
        return "BALANCED"

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
            "Текущий лидер:\n"
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

    def _profile_text(self, row: dict, title: str) -> str:
        p = row["params"]
        src = row.get("source", "applied analysis profile")
        return (
            f"{title}:\n"
            f"- source: {src}\n- score: {row['score']:.2f}\n- pnl: {row['pnl']:.2f}\n- winrate: {row['winrate']:.1f}%\n"
            f"- cycles: {row['cycles']}\n- losses: {row['losses']}\n- stuck: {row['exit_stuck']}\n"
            "- key settings:\n"
            f"  target={p['stream_target_ticks']} min_profit={p['stream_min_profit_ticks']} sell_timeout={p['stream_sell_timeout_ms']}\n"
            f"  retry={p['stream_sell_retry_max']} step={p['stream_sell_retry_step_ticks']} fast_buy={p['buy_timeout_ms_fast']}\n"
            f"  spread={p['min_spread_ticks']} chase={p['entry_chase_ticks']} active={p['stream_max_active_buys']} recycle={p['stream_recycle_delay_ms']}"
        )

    def _update_baseline_panel(self) -> None:
        baseline = self.applied_baseline or self.baseline_profile
        self.baseline_label.setText(self._profile_text(baseline, "Текущий / базовый профиль"))

    def select_profile_from_table(self) -> None:
        if self.selected_profile is None:
            self.log.append("Профиль не выбран")
            return
        self.log.append("Профиль выбран")

    def select_leader_profile(self) -> None:
        if self.runtime is None:
            return
        rows = self.runtime.top_rows(1)
        if not rows:
            return
        self.selected_profile = rows[0]
        self.log.append("Лидер выбран")

    def _apply_profile(self, row: dict, leader_apply: bool = False) -> None:
        change_lines = []
        current = asdict(SETTINGS_STORE.load())
        for key in ALLOWED_APPLY_FIELDS:
            old = current.get(key)
            new = row["params"].get(key)
            if old != new:
                change_lines.append(f"{key}: {old} -> {new}")
        risk = "LOW" if row.get("degradation_score", 0) <= 0 else ("MEDIUM" if row.get("degradation_score", 0) < 5 else "HIGH")
        expected = f"Expected: score {row.get("score",0):.2f}, pnl {row.get("pnl",0):.2f}, winrate {row.get("winrate",0):.1f}%"
        confirm = QMessageBox.question(self, "Подтверждение", "Применить профиль в settings.json?\n\nРиск: " + risk + "\n" + expected + "\n\n" + "\n".join(change_lines[:40]))
        if confirm != QMessageBox.StandardButton.Yes:
            return
        src = Path(SETTINGS_STORE.path)
        backup = src.with_name("settings.backup.analysis.json")
        if src.exists():
            backup.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        current_payload = current
        for key in ALLOWED_APPLY_FIELDS:
            current_payload[key] = row["params"][key]
        updated = replace(SETTINGS_STORE.load(), **current_payload)
        SETTINGS_STORE.save(updated)
        self.settings = updated
        applied = dict(row)
        applied["source"] = "applied analysis profile"
        self.applied_baseline = applied
        self._update_baseline_panel()
        self.log.append("Профиль применён и стал базовым для сравнения")
        self.log.append("Базовый профиль обновлён")
        if leader_apply:
            self.log.append("Лидер применён в settings.json")

    def apply_selected_profile(self) -> None:
        if self.selected_profile is None:
            self.log.append("Профиль не выбран")
            return
        self._apply_profile(self.selected_profile)

    def apply_leader_profile(self) -> None:
        if self.runtime is None:
            return
        rows = self.runtime.top_rows(1)
        if not rows:
            return
        self.selected_profile = rows[0]
        self._apply_profile(rows[0], leader_apply=True)

    def on_tick(self) -> None:
        if self.runtime is None:
            return
        self.runtime.tick(budget=80)
        rows = self.runtime.top_rows(100)
        self.table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            wr = f"{row['winrate']:.1f}%"
            summary = self._format_summary(row["params"])
            vals = [str(i + 1), f"{row['pnl']:.2f}", wr, str(row['cycles']), str(row['wins']), str(row['losses']), str(row['exit_stuck']), str(row['timeouts']), f"{row['score']:.2f}", row.get("profile_class", self._profile_kind(row)), row.get("params", {}).get("tournament", row.get("tournament", "BALANCED")), row.get("mutation_tier", "SMALL"), row.get("degradation_state", "PROFILE_STABLE"), str(row.get("generation", 0)), row.get("lineage", "SEED"), f"{row.get('stability_score',0):.1f}", row["params"].get("mutation_type", "BASE"), summary]
            color = self._row_color(row)
            for col, v in enumerate(vals):
                item = QTableWidgetItem(v)
                item.setBackground(color)
                self.table.setItem(i, col, item)
        if rows:
            self._update_best_panel(rows[0])
            self._update_leader_notice(rows[0])
        for event in self.runtime.pull_events():
            self.log.append(event)
        st = self.runtime.stats()
        self.stats.setText(
            f"Активных профилей: {st['active']} | Проверено: {st['tested']} | Скорость симуляции: {st['sim_speed']:.1f}/сек | "
            f"Время работы: {st['runtime_s']:.1f} сек | Лучший PnL: {st['best_pnl']:.2f} | Средний PnL: {st['avg_pnl']:.2f} | "
            f"Лучший Winrate: {st['best_winrate']:.1f}% | Циклов/час: {st['cycles_per_hour']:.0f}"
        )

    def _update_leader_notice(self, leader: dict) -> None:
        baseline = self.applied_baseline or self.baseline_profile
        base_score = baseline["score"]
        score_up = ((leader["score"] - base_score) / abs(base_score) * 100.0) if abs(base_score) > 0 else (100.0 if leader["score"] > 0 else 0.0)
        better = score_up >= 5.0 or (leader["pnl"] > baseline["pnl"] and leader["losses"] <= baseline["losses"] and leader["exit_stuck"] <= baseline["exit_stuck"])
        if not better:
            self.notice_label.setText("")
            return
        if self.last_notified_leader_id != leader["id"]:
            self.last_notified_leader_id = leader["id"]
            self.log.append("Новый лидер лучше базового профиля")
        self.notice_label.setText(
            "Найден профиль лучше применённого:\n"
            f"+{score_up:.1f}% к рейтингу\n"
            f"+{leader['pnl'] - baseline['pnl']:.2f} PnL\n"
            f"Winrate: +{leader['winrate'] - baseline['winrate']:.1f}%\n"
            f"Застреваний: -{max(0, baseline['exit_stuck'] - leader['exit_stuck'])}\n"
            f"Таймаутов: -{max(0, baseline['timeouts'] - leader['timeouts'])}"
        )
