"""Durable SQLite archive of verified closed futures candles.

This archive is deliberately separate from the bounded contiguous warm-up cache.
Rows remain available for diagnostics after a gap, but are never loaded directly
into indicators. Duplicate writes are idempotent by (symbol, interval, open_time).
"""
import math
import os
import sqlite3


def _connect(path):
    if not isinstance(path, str) or not path:
        raise ValueError("archive path must be nonempty")
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    db = sqlite3.connect(path, timeout=10.0)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=FULL")
    db.execute("""
        CREATE TABLE IF NOT EXISTS closed_candles (
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
    db.execute("""
        CREATE INDEX IF NOT EXISTS idx_closed_candles_symbol_time
        ON closed_candles(symbol, interval_ms, open_time)
    """)
    return db


def _validated_rows(symbol, rows, interval_ms):
    if not isinstance(symbol, str) or not symbol.endswith("_USDT"):
        raise ValueError("invalid futures symbol")
    if isinstance(interval_ms, bool) or not isinstance(interval_ms, int) or interval_ms <= 0:
        raise ValueError("invalid candle interval")
    if not isinstance(rows, (list, tuple)):
        raise ValueError("rows must be a list")
    result = []
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) != 6:
            raise ValueError("each closed candle must contain six fields")
        stamp = row[0]
        if isinstance(stamp, bool) or not isinstance(stamp, (int, float)):
            raise ValueError("invalid candle timestamp")
        stamp = int(stamp)
        if stamp <= 0 or stamp % interval_ms:
            raise ValueError("candle timestamp is not interval-aligned")
        values = []
        for value in row[1:]:
            if isinstance(value, bool):
                raise ValueError("invalid OHLCV value")
            number = float(value)
            if not math.isfinite(number) or number < 0:
                raise ValueError("OHLCV values must be finite and nonnegative")
            values.append(number)
        op, high, low, close, volume = values
        if min(op, high, low, close) <= 0 or high < max(op, close, low) or low > min(op, close, high):
            raise ValueError("invalid OHLC relationship")
        result.append((symbol, interval_ms, stamp, op, high, low, close, volume))
    return result


def record_closed_candles(path, symbol, rows, interval_ms=60_000):
    """Persist complete closed rows; repeated snapshots never double-count."""
    values = _validated_rows(symbol, rows, interval_ms)
    if not values:
        return count_closed_candles(path, symbol, interval_ms)
    db = _connect(path)
    try:
        with db:
            db.executemany(
                "INSERT OR IGNORE INTO closed_candles "
                "(symbol, interval_ms, open_time, open, high, low, close, volume) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", values
            )
            return db.execute(
                "SELECT COUNT(*) FROM closed_candles WHERE symbol=? AND interval_ms=?",
                (symbol, interval_ms),
            ).fetchone()[0]
    finally:
        db.close()


def count_closed_candles(path, symbol, interval_ms=60_000):
    """Return the durable distinct-candle count, independent of warm-up state."""
    if not os.path.isfile(path):
        return 0
    db = _connect(path)
    try:
        return db.execute(
            "SELECT COUNT(*) FROM closed_candles WHERE symbol=? AND interval_ms=?",
            (symbol, interval_ms),
        ).fetchone()[0]
    finally:
        db.close()
