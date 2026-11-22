"""Small, privacy-conscious crash log for Android/Kivy app diagnostics.

This module records uncaught exception type, sanitized message, and stack
locations only. It deliberately does not serialize locals, request headers,
credentials, or full traceback source lines.
"""
import logging
import os
import re
import sys
import threading
import traceback
from logging.handlers import RotatingFileHandler


_LOGGER = logging.getLogger("futures_analyzer.crash")
_INSTALLED = False
_LOG_PATH = None


def _safe_message(value):
    text = str(value or "")
    # Remove common secret-bearing query parameters and key/value fragments.
    text = re.sub(
        r"(?i)(api[_-]?key|secret|signature|token|password)=([^&\s,;]+)",
        r"\1=[REDACTED]", text,
    )
    text = re.sub(r"(?i)bearer\s+[A-Za-z0-9._~+/-]+=*", "Bearer [REDACTED]", text)
    return text[:500]


def _record(kind, exc_type, exc_value, exc_tb):
    try:
        frames = traceback.extract_tb(exc_tb) if exc_tb else []
        locations = [
            "{}:{} in {}".format(frame.filename, frame.lineno, frame.name)
            for frame in frames[-12:]
        ]
        _LOGGER.error(
            "%s | %s | %s | stack=%s",
            kind,
            getattr(exc_type, "__name__", "UnknownException"),
            _safe_message(exc_value),
            " <- ".join(locations) or "unavailable",
        )
    except Exception:
        # Diagnostics must never crash the application.
        pass


def install_crash_logging(directory):
    """Install process and thread exception hooks once; return the log path."""
    global _INSTALLED, _LOG_PATH
    try:
        os.makedirs(directory, exist_ok=True)
        _LOG_PATH = os.path.join(directory, "app-crash.log")
        if not _INSTALLED:
            handler = RotatingFileHandler(
                _LOG_PATH, maxBytes=256 * 1024, backupCount=2, encoding="utf-8"
            )
            handler.setFormatter(logging.Formatter(
                "%(asctime)s | %(levelname)s | %(message)s"
            ))
            _LOGGER.setLevel(logging.ERROR)
            _LOGGER.addHandler(handler)
            _LOGGER.propagate = False

            def main_hook(exc_type, exc_value, exc_tb):
                _record("MAIN", exc_type, exc_value, exc_tb)

            sys.excepthook = main_hook

            if hasattr(threading, "excepthook"):
                def thread_hook(args):
                    _record(
                        "THREAD:" + str(getattr(args.thread, "name", "unknown")),
                        args.exc_type, args.exc_value, args.exc_traceback,
                    )
                threading.excepthook = thread_hook

            _INSTALLED = True
        return _LOG_PATH
    except Exception:
        return None


def crash_log_status():
    """Return a compact status string suitable for the diagnostics screen."""
    if not _LOG_PATH:
        return "NOT INSTALLED"
    try:
        if not os.path.isfile(_LOG_PATH):
            return "READY; no uncaught errors recorded"
        size = os.path.getsize(_LOG_PATH)
        with open(_LOG_PATH, "r", encoding="utf-8", errors="replace") as stream:
            lines = stream.readlines()
        latest = lines[-1].strip() if lines else "empty"
        return "AVAILABLE; {} bytes; latest: {}".format(size, latest[:240])
    except Exception as exc:
        return "READ ERROR: " + type(exc).__name__
