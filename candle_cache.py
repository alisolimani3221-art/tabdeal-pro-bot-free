"""Strict local cache for verified one-minute closed candles.

This cache is only a short-restart optimization. It never fills missing market
history: restoration is accepted only when the cache was saved in the current
minute and its final closed candle is exactly the preceding minute.
"""
import json
import math
import os

CACHE_VERSION = 1
DEFAULT_INTERVAL_MS = 60_000
MIN_RESTORED_CANDLES = 51
MAX_CACHED_CANDLES = 500
MAX_CACHE_AGE_MS = 60_000


def _valid_row(row, interval_ms):
    if not isinstance(row, (list, tuple)) or len(row) < 6:
        return None
    try:
        timestamp = row[0]
        if isinstance(timestamp, bool) or not isinstance(timestamp, int):
            return None
        if timestamp <= 0 or timestamp % interval_ms:
            return None
        values = []
        for raw in row[1:6]:
            if isinstance(raw, bool):
                return None
            value = float(raw)
            if not math.isfinite(value):
                return None
            values.append(value)
        open_price, high, low, close, volume = values
        if min(open_price, high, low, close) <= 0 or volume < 0:
            return None
        if high < max(open_price, close, low) or low > min(open_price, close, high):
            return None
        return [timestamp, open_price, high, low, close, volume]
    except (TypeError, ValueError, OverflowError):
        return None


def _validated_rows(rows, interval_ms, minimum=1):
    if not isinstance(rows, (list, tuple)) or not minimum <= len(rows) <= MAX_CACHED_CANDLES:
        return None
    result = []
    previous = None
    for raw in rows:
        row = _valid_row(raw, interval_ms)
        if row is None or (previous is not None and row[0] != previous + interval_ms):
            return None
        result.append(row)
        previous = row[0]
    return result


def save_candle_cache(path, symbol, candles, saved_at_ms=None,
                      interval_ms=DEFAULT_INTERVAL_MS):
    """Atomically persist contiguous closed candles; return True on success."""
    if (not isinstance(symbol, str) or not symbol
            or isinstance(interval_ms, bool) or not isinstance(interval_ms, int)
            or interval_ms <= 0):
        return False
    rows = _validated_rows(candles, interval_ms, minimum=1)
    if rows is None:
        return False
    try:
        saved_at = int(saved_at_ms)
        if saved_at <= 0:
            return False
        current_bucket = saved_at - saved_at % interval_ms
        if rows[-1][0] + interval_ms != current_bucket:
            return False
        payload = {
            "version": CACHE_VERSION,
            "symbol": symbol,
            "interval_ms": interval_ms,
            "saved_at_ms": saved_at,
            "candles": rows,
        }
        parent = os.path.dirname(os.path.abspath(path))
        os.makedirs(parent, exist_ok=True)
        temporary = path + ".tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, separators=(",", ":"), allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        return True
    except (OSError, TypeError, ValueError, OverflowError):
        try:
            if os.path.exists(path + ".tmp"):
                os.remove(path + ".tmp")
        except OSError:
            pass
        return False


def load_candle_cache(path, symbol, now_ms=None,
                      interval_ms=DEFAULT_INTERVAL_MS,
                      max_age_ms=MAX_CACHE_AGE_MS):
    """Load only if the cache bridges directly into the current minute."""
    try:
        if (not isinstance(symbol, str) or not symbol
                or isinstance(interval_ms, bool) or not isinstance(interval_ms, int)
                or interval_ms <= 0 or isinstance(max_age_ms, bool)
                or not isinstance(max_age_ms, int) or max_age_ms < 0):
            return None
        now = int(now_ms)
        if now <= 0 or os.path.getsize(path) > 1_000_000:
            return None
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            return None
        if (payload.get("version") != CACHE_VERSION
                or payload.get("symbol") != symbol
                or payload.get("interval_ms") != interval_ms):
            return None
        saved_at = payload.get("saved_at_ms")
        if isinstance(saved_at, bool) or not isinstance(saved_at, int):
            return None
        age = now - saved_at
        if age < 0 or age > max_age_ms:
            return None
        current_bucket = now - now % interval_ms
        if saved_at - saved_at % interval_ms != current_bucket:
            return None
        rows = _validated_rows(
            payload.get("candles"), interval_ms, minimum=MIN_RESTORED_CANDLES
        )
        if rows is None or rows[-1][0] + interval_ms != current_bucket:
            return None
        return rows
    except (OSError, ValueError, TypeError, OverflowError):
        return None


def clear_candle_cache(path):
    """Remove a cache after a detected gap or invalid data."""
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except OSError:
        pass
