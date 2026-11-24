"""Persist validated one-minute candles for markets seen on the public broadcast."""
import os
import re
import threading
import time

from candle_history import load_candle_cache, save_candle_cache, validate_closed_candles
from market_candles import TradeCandleBuilder


class MultiSymbolCandleCache:
    """Side-cache other active symbols; never generates signals or orders."""
    def __init__(self, directory, allowed_symbols, max_symbols=256):
        self.directory = directory
        self.allowed_symbols = {
            value for value in allowed_symbols
            if isinstance(value, str) and re.fullmatch(r"[A-Z0-9]+_USDT", value)
        }
        self.max_symbols = max_symbols
        self.builders = {}
        self.last_saved = {}
        self.lock = threading.RLock()
        os.makedirs(directory, exist_ok=True)

    def _path(self, symbol):
        if symbol not in self.allowed_symbols:
            raise ValueError("symbol is not in the active futures allowlist")
        return os.path.join(self.directory, symbol + ".json")

    def _builder_for(self, symbol, now_ms):
        builder = self.builders.get(symbol)
        if builder is not None:
            return builder
        if len(self.builders) >= self.max_symbols:
            return None
        builder = TradeCandleBuilder(symbol)
        path = self._path(symbol)
        try:
            rows = load_candle_cache(path, symbol, now_ms,
                                     interval_ms=builder.interval_ms,
                                     limit=builder.closed.maxlen)
            if len(rows) >= 20:
                builder.restore_closed_candles(rows)
        except FileNotFoundError:
            pass
        except Exception:
            try:
                os.remove(path)
            except OSError:
                pass
        self.builders[symbol] = builder
        return builder

    def add_trade(self, trade, now_ms=None):
        if not isinstance(trade, dict):
            return {"accepted": False, "reason": "invalid_trade"}
        symbol = trade.get("symbol")
        if symbol not in self.allowed_symbols:
            return {"accepted": False, "reason": "symbol_not_allowed"}
        if now_ms is None:
            now_ms = int(time.time() * 1000)
        with self.lock:
            builder = self._builder_for(symbol, now_ms)
            if builder is None:
                return {"accepted": False, "reason": "builder_limit"}
            update = builder.add_trade(trade)
            path = self._path(symbol)
            if update.get("gap_detected"):
                try:
                    os.remove(path)
                except OSError:
                    pass
                self.last_saved.pop(symbol, None)
            elif update.get("accepted") and builder.closed:
                last_open = builder.closed[-1][0]
                if last_open != self.last_saved.get(symbol):
                    try:
                        rows = validate_closed_candles(
                            list(builder.closed), now_ms,
                            interval_ms=builder.interval_ms,
                            limit=builder.closed.maxlen)
                        if len(rows) >= 20:
                            save_candle_cache(path, symbol, rows)
                            self.last_saved[symbol] = rows[-1][0]
                    except (OSError, ValueError, TypeError):
                        pass
            return {"accepted": bool(update.get("accepted")),
                    "ready": bool(builder.is_ready),
                    "closed_candles": len(builder.closed),
                    "gap_detected": bool(update.get("gap_detected")),
                    "reason": update.get("reason")}
