"""Durable SQLite archive for finalized market candles.

Only finalized OHLCV rows are stored. Writes are idempotent by symbol,
interval and candle open time; conflicting duplicate rows fail closed.
"""
import math
import os
import sqlite3
import threading
from contextlib import contextmanager


class CandleArchiveError(RuntimeError):
    pass


class CandleArchive:
    def __init__(self, path, max_rows=5000):
        if not isinstance(path, str) or not path.strip():
            raise ValueError("archive path is required")
        if isinstance(max_rows, bool) or not isinstance(max_rows, int) or max_rows < 52:
            raise ValueError("max_rows must be an integer >= 52")
        self.path = os.path.abspath(path)
        self.max_rows = max_rows
        self._lock = threading.RLock()
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self._initialize()

    @contextmanager
    def _connection(self):
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            connection.execute("PRAGMA busy_timeout=10000")
            connection.execute("PRAGMA synchronous=FULL")
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self):
        try:
            with self._lock, self._connection() as db:
                db.execute("PRAGMA journal_mode=WAL")
                db.execute("""
                    CREATE TABLE IF NOT EXISTS candles (
                        symbol TEXT NOT NULL,
                        interval_ms INTEGER NOT NULL,
                        open_time INTEGER NOT NULL,
                        open REAL NOT NULL,
                        high REAL NOT NULL,
                        low REAL NOT NULL,
                        close REAL NOT NULL,
                        volume REAL NOT NULL,
                        PRIMARY KEY (symbol, interval_ms, open_time)
                    )
                """)
                db.execute("CREATE INDEX IF NOT EXISTS idx_candles_time ON candles(symbol, interval_ms, open_time DESC)")
        except (OSError, sqlite3.Error) as exc:
            raise CandleArchiveError("cannot initialize candle archive") from exc

    @staticmethod
    def _validate(symbol, interval_ms, row):
        if not isinstance(symbol, str) or not symbol:
            raise ValueError("symbol is required")
        if isinstance(interval_ms, bool) or not isinstance(interval_ms, int) or interval_ms <= 0:
            raise ValueError("interval_ms must be positive")
        if not isinstance(row, (list, tuple)) or len(row) != 6:
            raise ValueError("candle row must contain six OHLCV fields")
        values = []
        for value in row:
            if isinstance(value, bool):
                raise ValueError("boolean candle values are invalid")
            try:
                number = float(value)
            except (TypeError, ValueError, OverflowError):
                raise ValueError("candle values must be finite numbers")
            if not math.isfinite(number):
                raise ValueError("candle values must be finite numbers")
            values.append(number)
        if values[0] <= 0 or any(v <= 0 for v in values[1:5]) or values[5] < 0:
            raise ValueError("invalid candle timestamp or OHLCV value")
        if values[2] < max(values[1], values[4], values[3]) or values[3] > min(values[1], values[4]):
            raise ValueError("inconsistent candle high/low")
        return int(values[0]), *values[1:]

    def save_closed(self, symbol, interval_ms, row):
        values = self._validate(symbol, interval_ms, row)
        try:
            with self._lock, self._connection() as db:
                existing = db.execute(
                    "SELECT open,high,low,close,volume FROM candles WHERE symbol=? AND interval_ms=? AND open_time=?",
                    (symbol, interval_ms, values[0]),
                ).fetchone()
                if existing is not None:
                    if any(not math.isclose(float(a), float(b), rel_tol=1e-12, abs_tol=1e-12)
                           for a, b in zip(existing, values[1:])):
                        raise CandleArchiveError("conflicting duplicate closed candle")
                    return False
                db.execute(
                    "INSERT INTO candles(symbol,interval_ms,open_time,open,high,low,close,volume) VALUES(?,?,?,?,?,?,?,?)",
                    (symbol, interval_ms, *values),
                )
                db.execute(
                    "DELETE FROM candles WHERE symbol=? AND interval_ms=? AND open_time NOT IN "
                    "(SELECT open_time FROM candles WHERE symbol=? AND interval_ms=? ORDER BY open_time DESC LIMIT ?)",
                    (symbol, interval_ms, symbol, interval_ms, self.max_rows),
                )
            return True
        except CandleArchiveError:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise CandleArchiveError("could not durably save closed candle") from exc

    def load_recent(self, symbol, interval_ms, limit=None):
        count = self.max_rows if limit is None else limit
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise ValueError("limit must be positive")
        try:
            with self._lock, self._connection() as db:
                rows = db.execute(
                    "SELECT open_time,open,high,low,close,volume FROM candles "
                    "WHERE symbol=? AND interval_ms=? ORDER BY open_time DESC LIMIT ?",
                    (symbol, interval_ms, min(count, self.max_rows)),
                ).fetchall()
            return [list(row) for row in reversed(rows)]
        except (OSError, sqlite3.Error) as exc:
            raise CandleArchiveError("could not read candle archive") from exc
