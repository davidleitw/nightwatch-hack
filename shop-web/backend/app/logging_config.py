"""Application logging configuration.

The module only defines the logger and its configuration helpers.  Filesystem
access starts when ``configure_logging`` is called from the application
lifespan, so importing the FastAPI app remains side-effect free.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path


LOGGER_NAME = "shop"
DEFAULT_LOG_LEVEL = "INFO"
DEFAULT_LOG_DIR = "/logs"
LOG_FILE_NAME = "app.log"
LOG_BACKUP_COUNT = 7
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
_OWNED_HANDLER_MARKER = "_shop_backend_logging_handler"

logger = logging.getLogger(LOGGER_NAME)


class UTCFormatter(logging.Formatter):
    """Format every record with an ISO-8601 UTC timestamp."""

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        timestamp = datetime.fromtimestamp(record.created, tz=timezone.utc)
        if datefmt:
            return timestamp.strftime(datefmt)
        return timestamp.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _configured_level() -> int:
    configured = os.getenv("LOG_LEVEL", DEFAULT_LOG_LEVEL).strip().upper()
    if not configured:
        raise ValueError("LOG_LEVEL must be a valid logging level")

    level = getattr(logging, configured, None)
    if isinstance(level, int):
        return level

    try:
        numeric_level = int(configured)
    except ValueError as exc:
        raise ValueError(
            f"LOG_LEVEL must be a valid logging level, got {configured!r}"
        ) from exc
    if numeric_level < 0:
        raise ValueError(
            f"LOG_LEVEL must be a valid logging level, got {configured!r}"
        )
    return numeric_level


def _configured_log_dir() -> Path:
    configured = os.getenv("LOG_DIR", DEFAULT_LOG_DIR).strip()
    if not configured:
        raise ValueError("LOG_DIR must point to a writable directory")
    return Path(configured)


def _close_owned_handlers() -> None:
    for handler in list(logger.handlers):
        if getattr(handler, _OWNED_HANDLER_MARKER, False):
            logger.removeHandler(handler)
            handler.close()


def _mark_owned(handler: logging.Handler) -> logging.Handler:
    setattr(handler, _OWNED_HANDLER_MARKER, True)
    return handler


def configure_logging() -> logging.Logger:
    """Configure the application logger and return it.

    The root logger and loggers used by servers such as Uvicorn are left
    untouched.  Configuration errors are printed to stderr and raised so a
    bad logging setup cannot make startup appear successful.
    """

    handlers: list[logging.Handler] = []

    try:
        level = _configured_level()
        log_dir = _configured_log_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        formatter = UTCFormatter(LOG_FORMAT)

        stream_handler = _mark_owned(logging.StreamHandler(sys.stdout))
        stream_handler.setLevel(level)
        stream_handler.setFormatter(formatter)
        handlers.append(stream_handler)

        file_handler = _mark_owned(
            TimedRotatingFileHandler(
                filename=log_dir / LOG_FILE_NAME,
                when="midnight",
                interval=1,
                backupCount=LOG_BACKUP_COUNT,
                encoding="utf-8",
                utc=True,
            )
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        handlers.append(file_handler)
    except Exception as exc:
        for handler in handlers:
            handler.close()
        print(f"Logging initialization failed: {exc}", file=sys.stderr)
        raise RuntimeError("Unable to initialize application logging") from exc

    _close_owned_handlers()
    logger.setLevel(level)
    logger.propagate = False
    for handler in handlers:
        logger.addHandler(handler)
    return logger


def shutdown_logging() -> None:
    """Flush and close only handlers created by ``configure_logging``."""

    _close_owned_handlers()
