"""Conservative one-minute candle builder for normalized Tabdeal trade events.

Only validated last-trade events belong here. Order-book prices must never be
passed as trades. A timestamp gap or out-of-order event invalidates warm-up so
technical indicators cannot silently span missing market data.
"""
import math
import re
from collections import deque


class TradeCandleBuilder:
    def __init__(self, symbol, interval_ms=60_000, max_candles=500):
        if not isinstance(symbol, str) or not re.fullmatch(r"[A-Z0-9]+_USDT", symbol):
            raise ValueError("symbol must be an exchange-native USDT futures symbol")
        if isinstance(interval_ms, bool) or not isinstance(interval_ms, int) or interval_ms <= 0:
            raise ValueError("interval_ms must be a positive integer")
        if isinstance(max_candles, bool) or not isinstance(max_candles, int) or max_candles < 52:
            raise ValueError("max_candles must be an integer of at least 52")
        self.symbol = symbol
        self.interval_ms = interval_ms
        self.closed = deque(maxlen=max_candles)
        self.current = None
        self.last_timestamp_ms = None
        self.last_sequence = None
        self.rejected = 0
        self.gaps = 0
        # The first bucket after subscription/reconnect may be only partially observed.
        self.discard_next_close = True
        # Set only when a strictly validated local cache was restored.
        self.expected_next_bucket = None

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
            if self.expected_next_bucket is not None:
                if bucket < self.expected_next_bucket:
                    self.rejected += 1
                    return {"accepted": False, "reason": "before_cached_history"}
                if bucket > self.expected_next_bucket:
                    # The app was offline across a missing minute. Cached
                    # candles must not bridge that unobserved interval.
                    self.closed.clear()
                    self.gaps += 1
                    gap_detected = True
                    self.discard_next_close = True
                self.expected_next_bucket = None
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
                self.closed.append(self._as_row(self.current))
            else:
                self.discard_next_close = False
            if bucket != previous_bucket + self.interval_ms:
                self.gaps += 1
                gap_detected = True
                # Do not let indicators bridge an unknown interval.
                self.closed.clear()
                self.discard_next_close = True
            self.current = self._new_candle(bucket, price, quantity, timestamp)

        self.last_timestamp_ms = timestamp
        if sequence is not None:
            self.last_sequence = sequence
        return {"accepted": True, "gap_detected": gap_detected,
                "closed_candles": len(self.closed), "ready": self.is_ready}

    def reset_after_gap(self):
        """Discard all in-flight history after any reconnect that may miss trades."""
        self.closed.clear()
        self.current = None
        self.last_timestamp_ms = None
        self.last_sequence = None
        self.gaps += 1
        self.discard_next_close = True
        self.expected_next_bucket = None

    def restore_closed_candles(self, rows, expected_next_bucket=None):
        """Restore contiguous history; live data must resume in the validated bucket."""
        if not isinstance(rows, (list, tuple)) or len(rows) < 51:
            raise ValueError("at least 51 closed candles are required")
        normalized = []
        previous = None
        for row in rows:
            if not isinstance(row, (list, tuple)) or len(row) < 6:
                raise ValueError("invalid cached candle")
            timestamp = row[0]
            if (isinstance(timestamp, bool) or not isinstance(timestamp, int)
                    or timestamp <= 0 or timestamp % self.interval_ms):
                raise ValueError("invalid cached candle timestamp")
            try:
                values = [float(value) for value in row[1:6]]
            except (TypeError, ValueError, OverflowError):
                raise ValueError("invalid cached OHLCV values")
            if any(not math.isfinite(value) for value in values):
                raise ValueError("non-finite cached OHLCV values")
            open_price, high, low, close, volume = values
            if (min(open_price, high, low, close) <= 0 or volume < 0
                    or high < max(open_price, close, low)
                    or low > min(open_price, close, high)):
                raise ValueError("inconsistent cached OHLCV values")
            if previous is not None and timestamp != previous + self.interval_ms:
                raise ValueError("cached candles are not contiguous")
            normalized.append([timestamp] + values)
            previous = timestamp
        self.closed.clear()
        for row in normalized[-self.closed.maxlen:]:
            self.closed.append(row)
        self.current = None
        self.last_timestamp_ms = None
        self.last_sequence = None
        self.discard_next_close = True
        expected = (normalized[-1][0] + self.interval_ms
                    if expected_next_bucket is None else expected_next_bucket)
        if (isinstance(expected, bool) or not isinstance(expected, int)
                or expected % self.interval_ms
                or expected - normalized[-1][0] not in
                (self.interval_ms, 2 * self.interval_ms)):
            raise ValueError("cached history cannot bridge the expected live bucket")
        self.expected_next_bucket = expected

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
        return (len(self.closed) >= 51 and self.current is not None
                and not self.discard_next_close)

    def candles_for_analysis(self):
        """Return chronological closed candles plus current forming candle."""
        rows = list(self.closed)
        if self.current is not None:
            rows.append(self._as_row(self.current))
        return rows

    def status(self):
        return {"symbol": self.symbol, "closed_candles": len(self.closed),
                "has_forming_candle": self.current is not None,
                "ready": self.is_ready, "gaps": self.gaps,
                "rejected_trades": self.rejected}
