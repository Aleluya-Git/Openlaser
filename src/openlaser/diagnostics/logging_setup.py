"""Registro estructurado (JSON por línea) sin bloquear la interfaz.

Los ``logging.Handler`` de la aplicación escriben en una cola en memoria; un
``QueueListener`` en segundo plano vuelca a disco con rotación.
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import queue
import time
from pathlib import Path

_listener: logging.handlers.QueueListener | None = None


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(record.created)) + f".{int(record.msecs):03d}",
            "level": record.levelname,
            "logger": record.name,
            "thread": record.threadName,
            "msg": record.getMessage(),
        }
        kind = getattr(record, "safety_kind", None)
        if kind:
            data["safety_kind"] = kind
        if record.exc_info:
            data["exc"] = self.formatException(record.exc_info)
        return json.dumps(data, ensure_ascii=False)


def setup_logging(log_dir: Path, level: int = logging.INFO) -> Path:
    global _listener
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / "openlaser.jsonl"
    file_handler = logging.handlers.RotatingFileHandler(path, maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    file_handler.setFormatter(JsonFormatter())
    q: "queue.Queue[logging.LogRecord]" = queue.Queue(-1)
    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(logging.handlers.QueueHandler(q))
    if _listener is not None:
        _listener.stop()
    _listener = logging.handlers.QueueListener(q, file_handler, respect_handler_level=False)
    _listener.start()
    logging.getLogger(__name__).info("Registro iniciado en %s", path)
    return path


def shutdown_logging() -> None:
    global _listener
    if _listener is not None:
        _listener.stop()
        _listener = None
