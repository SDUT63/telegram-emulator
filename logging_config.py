from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone

_CONFIGURED = False


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = record.exc_info[0].__name__ if record.exc_info[0] else "Exception"
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_logging() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    level_name = (os.getenv("SDUT_LOG_LEVEL") or "INFO").upper()
    level = getattr(logging, level_name, None)
    if not isinstance(level, int):
        raise ValueError("SDUT_LOG_LEVEL must be a valid logging level")
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
    _CONFIGURED = True
