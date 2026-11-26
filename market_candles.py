"""Conservative one-minute candle builder for normalized Tabdeal trade events.

Only validated last-trade events belong here. Order-book prices must never be
passed as trades. A timestamp gap or out-of-order event invalidates warm-up so
technical indicators cannot silently span missing market data.
"""
import math
import re
from collections import deque


class TradeCandleBuilder:
    def __init__(self, symbol, interval_ms=60_000, max_candles=500, archive=None):
        if not isinstance(symbol, str) or not re.fullmatch(r"[A-Z0-9]+_USDT", symbol):
            raise ValueError("symbol must be an exchange-native USDT futures symbol")
        if isinstance(interval_ms, bool) or not isinstance(interval_ms, int) or interval_ms <= 0:
            raise ValueError("interval_ms must be a positive integer")
        if isinstance(max_candles, bool) or not isinstance(max_candles, int) or max_candles < 52:
            raise ValueError("max_candles must be an integer of at least 52")
        self.symbol = symbol
        self.interval_ms = interval_ms
        # `closed` is the contiguous warm-up window used by indicators.
        # `history` is the retained archive shown in diagnostics; a data gap
        # must invalidate continuity without erasing already closed candles.
        self.closed = deque(maxlen=max_candles)
        self.archive = archive
        self.archive_error = None
        self.history = deque(maxlen=max_candles)
        if self.archive is not None:
            for archived_row in self.archive.load_recent(symbol, interval_ms, max_candles):
                self.history.append(archived_row)
        self.current = None
        self.last_timestamp_ms = None
        self.last_sequence = None
        self.rejected = 0
        self.gaps = 0
        # The first bucket after subscription/reconnect may be only partially observed.
        self.discard_next_close = True

    @staticmethod
    def _positive(value, name):
        if isinstance(value, bool):
            raise ValueError("{} must be positive and finite".format(name))
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            raise ValueError("{} must be positive and finite".format(name))
        if not math.isfinite(number) or number <= 0:
            raise ValueError("{} must be positive and finite".format(name))
        return number

    def add_trade(self, trade):
        """Add one normalized trade; return a compact acceptance diagnostic."""
        if not isinstance(trade, dict) or trade.get("ok") is False:
            self.rejected += 1
            return {"accepted": False, "reason": "invalid_trade"}
        if str(trade.get("symbol", "")).upper() != self.symbol:
            self.rejected += 1
            return {"accepted": False, "reason": "symbol_mismatch"}
        try:
            price = self._positive(trade.get("price"), "price")
            quantity = self._positive(trade.get("quantity"), "quantity")
            timestamp = trade.get("timestamp_ms")
            if isinstance(timestamp, bool) or not isinstance(timestamp, int) or timestamp <= 0:
                raise ValueError("timestamp_ms must be a positive integer")
            if timestamp > 10_000_000_000_000:
                raise ValueError("timestamp_ms is outside supported range")
            sequence = trade.get("sequence")
            if sequence is not None and (isinstance(sequence, bool) or
                    not isinstance(sequence, int) or sequence < 0):
                raise ValueError("sequence must be a nonnegative integer")
        except (ValueError, TypeError, OverflowError):
            self.rejected += 1
            return {"accepted": False, "reason": "invalid_fields"}

        if self.last_timestamp_ms is not None and timestamp < self.last_timestamp_ms:
            self.rejected += 1
            return {"accepted": False, "reason": "out_of_order"}
        if (sequence is not None and self.last_sequence is not None
                and sequence <= self.last_sequence):
            self.rejected += 1
            return {"accepted": False, "reason": "duplicate_or_old_sequence"}

        bucket = timestamp - (timestamp % self.interval_ms)
        gap_detected = False
        if self.current is None:
            self.current = self._new_candle(bucket, price, quantity, timestamp)
        elif bucket < self.current["open_time"]:
            self.rejected += 1
            return {"accepted": False, "reason": "out_of_order"}
        elif bucket == self.current["open_time"]:
            if timestamp < self.current["last_timestamp"]:
                self.rejected += 1
                return {"accepted": False, "reason": "out_of_order"}
            self._update_candle(self.current, price, quantity, timestamp)
        else:
            previous_bucket = self.current["open_time"]
            if not self.discard_next_close:
                closed_row = self._as_row(self.current)
                try:
                    if self.archive is not None:
                        self.archive.save_closed(self.symbol, self.interval_ms, closed_row)
                    self.closed.append(closed_row)
                    self.history.append(closed_row)
                    self.archive_error = None
                except Exception as exc:
                    # Never generate trading signals from candles that failed
                    # durable persistence. Keep the app alive for diagnostics.
                    self.archive_error = type(exc).__name__
            else:
                self.discard_next_close = False
            if bucket != previous_bucket + self.interval_ms:
                self.gaps += 1
                gap_detected = True
                # Do not let indicators bridge an unknown interval. Keep
                # archived closed candles; only reset the contiguous window.
                self.closed.clear()
                self.discard_next_close = True
            self.current = self._new_candle(bucket, price, quantity, timestamp)

        self.last_timestamp_ms = timestamp
        if sequence is not None:
            self.last_sequence = sequence
        return {"accepted": True, "gap_detected": gap_detected,
                "closed_candles": len(self.closed), "ready": self.is_ready}

    def reset_after_gap(self):
        """Invalidate continuity after reconnect while retaining closed history."""
        self.closed.clear()
        self.current = None
        self.last_timestamp_ms = None
        self.last_sequence = None
        self.gaps += 1
        self.discard_next_close = True

    @staticmethod
    def _new_candle(bucket, price, quantity, timestamp):
        return {"open_time": bucket, "open": price, "high": price,
                "low": price, "close": price, "volume": quantity,
                "last_timestamp": timestamp}

    @staticmethod
    def _update_candle(candle, price, quantity, timestamp):
        candle["high"] = max(candle["high"], price)
        candle["low"] = min(candle["low"], price)
        candle["close"] = price
        candle["volume"] += quantity
        candle["last_timestamp"] = timestamp

    @staticmethod
    def _as_row(candle):
        return [candle["open_time"], candle["open"], candle["high"],
                candle["low"], candle["close"], candle["volume"]]

    @property
    def is_ready(self):
        # analyze_klines excludes the final row as a still-forming candle.
        return (self.archive_error is None and len(self.closed) >= 51
                and self.current is not None)

    def candles_for_analysis(self):
        """Return chronological closed candles plus current forming candle."""
        rows = list(self.closed)
        if self.current is not None:
            rows.append(self._as_row(self.current))
        return rows

    def status(self):
        return {"symbol": self.symbol, "closed_candles": len(self.closed),
                "stored_closed_candles": len(self.history),
                "has_forming_candle": self.current is not None,
                "ready": self.is_ready, "archive_error": self.archive_error,
                "gaps": self.gaps,
                "rejected_trades": self.rejected}
