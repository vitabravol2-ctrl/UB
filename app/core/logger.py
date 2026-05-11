from datetime import datetime, timezone


def format_log(tag: str, message: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3]
    return f"[{ts}] [{tag}] {message}"
