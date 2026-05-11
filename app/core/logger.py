from datetime import datetime, timezone
from pathlib import Path
import csv


class FileLogManager:
    def __init__(self, base_dir: str = "logs", max_size_bytes: int = 20 * 1024 * 1024) -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.max_size_bytes = max_size_bytes

    def _dated_path(self, prefix: str, ext: str) -> Path:
        date_str = datetime.now().strftime("%Y-%m-%d")
        base = self.base_dir / f"{prefix}_{date_str}.{ext}"
        if not base.exists() or base.stat().st_size < self.max_size_bytes:
            return base
        idx = 1
        while True:
            candidate = self.base_dir / f"{prefix}_{date_str}_{idx}.{ext}"
            if not candidate.exists() or candidate.stat().st_size < self.max_size_bytes:
                return candidate
            idx += 1

    def write_trade(self, line: str) -> None:
        path = self._dated_path("trade", "log")
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def write_system(self, line: str) -> None:
        path = self._dated_path("system", "log")
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def write_cycle(self, row: dict) -> None:
        path = self._dated_path("cycles", "csv")
        is_new = not path.exists()
        with path.open("a", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "cycle_id",
                    "buy_order_id",
                    "sell_order_id",
                    "buy_time",
                    "sell_time",
                    "buy_avg_price",
                    "sell_avg_price",
                    "qty_filled",
                    "buy_u",
                    "sell_u",
                    "fee_u",
                    "pnl_u",
                    "duration_ms",
                    "status",
                ],
            )
            if is_new:
                writer.writeheader()
            writer.writerow(row)


def format_log(tag: str, message: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3]
    return f"[{ts}] [{tag}] {message}"
