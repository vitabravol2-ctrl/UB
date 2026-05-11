import time
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.core.config import CONFIG
from app.core.logger import format_log
from app.core.market_rest import MarketREST
from app.core.market_state import MarketState
from app.core.market_ws import MarketWSClient
from app.gui.styles import PALETTE, main_qss
from app.gui.widgets import big_value, kv_card


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("UB v0.1.0 / BTCU Spread Harvester")
        self.resize(1400, 860)
        self.setStyleSheet(main_qss())

        self.state = MarketState()
        self.rest = MarketREST()
        self.ws = MarketWSClient(CONFIG.symbol)
        self.started_watch_ms = int(time.time() * 1000)

        root = QWidget()
        self.setCentralWidget(root)
        self.main_layout = QVBoxLayout(root)

        self.top_status = QLabel()
        self.top_status.setStyleSheet("font-size: 15px; font-weight: 700;")
        self.main_layout.addWidget(self.top_status)

        self.grid = QGridLayout()
        self.main_layout.addLayout(self.grid)

        self._build_cards()
        self._build_controls()
        self._build_logs()

        self.ws.signals.book.connect(self.on_ws_book)
        self.ws.signals.status.connect(self.on_ws_status)
        self.ws.signals.log.connect(self.log)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.on_tick)
        self.timer.start(300)

        self.rest_timer = QTimer(self)
        self.rest_timer.timeout.connect(self.fetch_rest)
        self.rest_timer.start(CONFIG.rest_poll_ms)

        self.log("BOOT", "UB v0.1.0 started")

    def _build_cards(self) -> None:
        conn, self.conn = kv_card("CONNECTION", [("API", "N/A"), ("WS", "CONNECTING"), ("REST", "N/A"), ("WS age ms", "N/A"), ("REST age ms", "N/A"), ("Ping ms", "N/A")])
        self.grid.addWidget(conn, 0, 0)

        bid_box, self.bid_v = big_value("BID", "N/A")
        ask_box, self.ask_v = big_value("ASK", "N/A")
        spr_box, self.spr_v = big_value("SPREAD U", "N/A")
        self.grid.addWidget(bid_box, 0, 1)
        self.grid.addWidget(ask_box, 0, 2)
        self.grid.addWidget(spr_box, 0, 3)

        engine, self.engine = kv_card("SPREAD ENGINE", [("Status", "BAD"), ("Spread", "N/A"), ("Capture estimate", "0.00"), ("Lifetime ms", "0"), ("Queue", "N/A")])
        self.grid.addWidget(engine, 1, 0)

        fsm, self.fsm = kv_card("RUNTIME / FSM", [("State", "IDLE"), ("Entry", "N/A"), ("Exit", "N/A"), ("Mode", "WATCH ONLY"), ("LIVE", "OFF")])
        risk, _ = kv_card("RISK GUARD", [("Max loss", "ON"), ("Panic", "READY"), ("Exposure", "LOW"), ("Stop", "ARMED")])
        bal, _ = kv_card("BALANCES", [("BTC free", "N/A"), ("U free", "N/A"), ("Locked", "N/A"), ("Max lot", "N/A")])
        self.grid.addWidget(fsm, 1, 1)
        self.grid.addWidget(risk, 1, 2)
        self.grid.addWidget(bal, 1, 3)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Entry price", "Exit price", "Qty", "Age", "PnL", "Status"])
        self.grid.addWidget(self.table, 2, 0, 1, 4)

    def _build_controls(self) -> None:
        row = QHBoxLayout()
        buttons = [
            ("CONNECT", None, self.connect_ws),
            ("START WATCH", "primary", None),
            ("START PAPER", None, None),
            ("ENABLE LIVE", None, None),
            ("START SINGLE CYCLE", None, None),
            ("START CONVEYOR", None, None),
            ("PAUSE", "warning", None),
            ("PANIC EXIT", "danger", None),
            ("CANCEL ALL", "danger", None),
            ("SETTINGS", None, None),
        ]
        for name, kind, cb in buttons:
            b = QPushButton(name)
            if kind:
                b.setProperty("kind", kind)
                b.style().unpolish(b)
                b.style().polish(b)
            if cb:
                b.clicked.connect(cb)
            else:
                b.setDisabled(True)
            row.addWidget(b)
        self.main_layout.addLayout(row)

    def _build_logs(self) -> None:
        self.logs = QTableWidget(0, 1)
        self.logs.setHorizontalHeaderLabels(["LOGS"])
        self.main_layout.addWidget(self.logs)

    def connect_ws(self) -> None:
        self.ws.start()

    def on_ws_book(self, bid: float, ask: float, ts: int) -> None:
        self.state.snapshot.bid = bid
        self.state.snapshot.ask = ask
        self.state.snapshot.updated_ms = ts
        self.state.snapshot.source = "WS"
        self.state.last_ws_ms = ts

    def on_ws_status(self, status: str) -> None:
        self.state.ws_status = status

    def fetch_rest(self) -> None:
        try:
            bid, ask, ts = self.rest.fetch_book_ticker(CONFIG.symbol)
            self.state.last_rest_ms = ts
            self.state.rest_status = "OK"
            ws_age = self.state.age_ms(self.state.last_ws_ms)
            if ws_age is None or ws_age > CONFIG.max_ws_age_ms:
                self.state.snapshot.bid = bid
                self.state.snapshot.ask = ask
                self.state.snapshot.updated_ms = ts
                self.state.snapshot.source = "REST"
            self.log("REST", "fallback snapshot OK")
        except Exception as exc:
            self.state.rest_status = "ERROR"
            self.log("REST", f"error: {exc}")

    def on_tick(self) -> None:
        self._refresh_ui()

    def _refresh_ui(self) -> None:
        bid = self.state.snapshot.bid
        ask = self.state.snapshot.ask
        spread = self.state.snapshot.spread
        ws_age = self.state.age_ms(self.state.last_ws_ms)
        rest_age = self.state.age_ms(self.state.last_rest_ms)

        self.conn["WS"].setText(self.state.ws_status)
        self.conn["REST"].setText(self.state.rest_status)
        self.conn["WS age ms"].setText("N/A" if ws_age is None else str(ws_age))
        self.conn["REST age ms"].setText("N/A" if rest_age is None else str(rest_age))

        self.bid_v.setText("N/A" if bid is None else f"{bid:.2f}")
        self.ask_v.setText("N/A" if ask is None else f"{ask:.2f}")
        self.spr_v.setText("N/A" if spread is None else f"{spread:.2f}")

        status = "BAD"
        capture = 0.0
        if spread is not None:
            capture = max(spread - CONFIG.entry_offset - CONFIG.exit_offset, 0.0)
            if spread >= CONFIG.min_spread * 1.5:
                status = "HOT"
            elif spread >= CONFIG.min_spread:
                status = "READY"
            elif spread > 0:
                status = "WATCH"

        if spread is not None and status == "HOT":
            self.log("SPREAD", f"HOT spread={spread:.2f}U capture={capture:.2f}U")

        self.engine["Status"].setText(status)
        self.engine["Spread"].setText("N/A" if spread is None else f"{spread:.2f}")
        self.engine["Capture estimate"].setText(f"{capture:.2f}")
        self.engine["Lifetime ms"].setText(str(max(int(time.time() * 1000) - self.started_watch_ms, 0)))

        if bid is not None and ask is not None:
            self.fsm["Entry"].setText(f"{bid + CONFIG.entry_offset:.2f}")
            self.fsm["Exit"].setText(f"{ask - CONFIG.exit_offset:.2f}")
            self.fsm["State"].setText("WATCH_SPREAD")

        ws_txt = "●" if self.state.ws_status == "OK" else "○"
        rest_txt = "●" if self.state.rest_status == "OK" else "○"
        spread_hot = "HOT" if status == "HOT" else status
        self.top_status.setText(
            f"{CONFIG.symbol} | WS {ws_txt} {self.state.ws_status} | REST {rest_txt} {self.state.rest_status} | API N/A | LIVE OFF | SPREAD {spread_hot}"
        )

    def log(self, tag: str, message: str) -> None:
        text = format_log(tag, message)
        row = self.logs.rowCount()
        self.logs.insertRow(row)
        self.logs.setItem(row, 0, QTableWidgetItem(text))
        if self.logs.rowCount() > 200:
            self.logs.removeRow(0)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self.ws.stop()
        super().closeEvent(event)
