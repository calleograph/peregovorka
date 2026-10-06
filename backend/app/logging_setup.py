"""Структурированные журналы: JSON, request_id, маскирование секретов."""
from __future__ import annotations

import contextvars
import json
import logging
import re
import sys
from datetime import datetime, timezone

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")

_SENSITIVE_KEYS = re.compile(r"(pass(word)?|secret|token|authorization|cookie|api[_-]?key|bind)", re.I)
_SENSITIVE_IN_TEXT = re.compile(r"(?i)\b(password|passwd|secret|token|authorization)\b(\s*[=:]\s*)([^\s,;&\"']+)")
_STANDARD_ATTRS = set(logging.LogRecord("x", 0, "x", 0, "", (), None).__dict__) | {"message", "asctime", "taskName"}


def scrub(value):
    """Рекурсивно маскирует значения по «чувствительным» ключам."""
    if isinstance(value, dict):
        return {k: ("***" if isinstance(k, str) and _SENSITIVE_KEYS.search(k) else scrub(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub(v) for v in value]
    if isinstance(value, str):
        return _SENSITIVE_IN_TEXT.sub(lambda m: f"{m.group(1)}{m.group(2)}***", value)
    return value


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = _SENSITIVE_IN_TEXT.sub(lambda m: f"{m.group(1)}{m.group(2)}***", record.msg)
        if record.args and isinstance(record.args, tuple):
            record.args = tuple(scrub(a) if isinstance(a, (str, dict, list)) else a for a in record.args)
        for key in list(record.__dict__):
            if key not in _STANDARD_ATTRS and _SENSITIVE_KEYS.search(key):
                record.__dict__[key] = "***"
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": request_id_var.get(),
        }
        for key, val in record.__dict__.items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                payload[key] = scrub(val)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class ConsoleFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = f"{self.formatTime(record, '%H:%M:%S')} {record.levelname:<7} [{request_id_var.get()}] {record.name}: {record.getMessage()}"
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return base


def configure_logging(level: str = "INFO", fmt: str = "json") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if fmt == "json" else ConsoleFormatter())
    handler.addFilter(RedactingFilter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    for noisy in ("uvicorn.access", "ldap3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    logging.getLogger("uvicorn.error").handlers.clear()
    logging.getLogger("uvicorn").handlers.clear()
    logging.getLogger("uvicorn").propagate = True
    logging.getLogger("uvicorn.error").propagate = True
