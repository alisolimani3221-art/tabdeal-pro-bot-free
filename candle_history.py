"""Validated, persistent cache for historical one-minute OHLCV candles.

This module deliberately accepts an injected history provider. It does not guess
an exchange REST endpoint or treat order-book snapshots as candle history.
"""
import json
import math
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed


def validate_closed_candles(rows, now_ms, interval_ms=60_000, limit=500):
    """Return the newest contiguous, closed OHLCV rows or raise ValueError."""
    if isinstance(now_ms, bool) or not isinstance(now_ms, int) or now_ms <= 0:
        raise ValueError("now_ms must be a positive integer")
    if isinstance(interval_ms, bool) or not isinstance(interval_ms, int) or interval_ms <= 0:
        raise ValueError("interval_ms must be a positive integer")
    if not isinstance(rows, (list, tuple)):
        raise ValueError("candles must be a list")
    bucket_now = now_ms - (now_ms % interval_ms)
    parsed = {}
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 6:
            raise ValueError("each candle must contain OHLCV fields")
        stamp = row[0]
        if isinstance(stamp, bool) or not isinstance(stamp, (int, float)):
            raise ValueError("candle open time must be numeric")
        stamp = int(stamp)
        if stamp <= 0 or stamp % interval_ms or stamp >= bucket_now:
            # The current, still-forming candle is never historical warm-up.
            continue
        values = []
        for value in row[1:6]:
            if isinstance(value, bool):
                raise ValueError("OHLCV values must be finite numbers")
            try:
                number = float(value)
            except (TypeError, ValueError, OverflowError):
                raise ValueError("OHLCV values must be finite numbers")
            if not math.isfinite(number) or number < 0:
                raise ValueError("OHLCV values must be finite and nonnegative")
            values.append(number)
        op, high, low, close, volume = values
        if min(op, high, low, close) <= 0 or volume < 0:
            raise ValueError("prices must be positive and volume nonnegative")
        if high < max(op, close, low) or low > min(op, close, high):
            raise ValueError("invalid OHLC relationship")
        parsed[stamp] = [stamp, op, high, low, close, volume]
    ordered = [parsed[key] for key in sorted(parsed)]
    # Duplicate timestamps collapse only if the payload agrees exactly.
    if len(ordered) >= 2:
        for left, right in zip(ordered, ordered[1:]):
            if right[0] - left[0] != interval_ms:
                raise ValueError("historical candle gap detected")
    return ordered[-limit:]


def save_candle_cache(path, symbol, rows):
    """Atomically persist validated rows; fsync file and parent directory."""
    if not isinstance(symbol, str) or not symbol.endswith("_USDT"):
        raise ValueError("invalid futures symbol")
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    payload = {"schema": 1, "symbol": symbol, "candles": rows}
    fd, temporary = tempfile.mkstemp(prefix=".candles-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, separators=(",", ":"), allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        flags = getattr(os, "O_DIRECTORY", 0) | os.O_RDONLY
        dir_fd = os.open(directory, flags)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def load_candle_cache(path, symbol, now_ms, interval_ms=60_000, limit=500):
    """Load only a matching schema/symbol cache and revalidate it on every read."""
    with open(path, "r", encoding="utf-8") as stream:
        payload = json.load(stream)
    if not isinstance(payload, dict) or payload.get("schema") != 1:
        raise ValueError("unsupported candle cache schema")
    if payload.get("symbol") != symbol:
        raise ValueError("candle cache symbol mismatch")
    return validate_closed_candles(payload.get("candles"), now_ms, interval_ms, limit)


def warm_symbols(symbols, fetch_history, now_ms, max_workers=4, interval_ms=60_000,
                 limit=500):
    """Fetch/validate symbols independently with bounded concurrency.

    fetch_history(symbol) must return historical OHLCV rows. One failed symbol
    does not block other symbols; no trade/order action is performed.
    """
    if isinstance(max_workers, bool) or not isinstance(max_workers, int) or not 1 <= max_workers <= 8:
        raise ValueError("max_workers must be between 1 and 8")
    unique = list(dict.fromkeys(symbols))
    results, errors = {}, {}
    def one(symbol):
        rows = fetch_history(symbol)
        valid = validate_closed_candles(rows, now_ms, interval_ms, limit)
        return valid
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(one, symbol): symbol for symbol in unique}
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                results[symbol] = future.result()
            except Exception as exc:
                errors[symbol] = "{}: {}".format(type(exc).__name__, str(exc)[:160])
    return {"results": results, "errors": errors, "requested": len(unique),
            "ready_count": sum(len(rows) >= 20 for rows in results.values()),
            "live_orders": False}
