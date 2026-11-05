"""One-line JSON logs (stdout) for the API and the MCP server; extra={...} fields on a record become JSON keys."""
from __future__ import annotations

import json
import logging
import sys

_STD = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime", "taskName"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {"ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"), "level": record.levelname, "logger": record.name, "msg": record.getMessage()}
        out |= {k: v for k, v in record.__dict__.items() if k not in _STD}
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str, ensure_ascii=False)


def configure_logging(level: str = "INFO", stream=None) -> None:
    """Install the JSON handler on the `veritas` logger (idempotent). Never logs prompts, documents or credentials: callers pass ids and counts only."""
    log = logging.getLogger("veritas")
    log.setLevel(level.upper())
    log.handlers = [h for h in log.handlers if not getattr(h, "_veritas", False)]
    h = logging.StreamHandler(stream or sys.stderr)
    h.setFormatter(JsonFormatter())
    h._veritas = True   # type: ignore[attr-defined]
    log.addHandler(h)
    log.propagate = False
