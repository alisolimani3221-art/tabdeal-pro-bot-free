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
        # Reuse closed candles only when the stream resumes in the same minute.
        self._resume_bucket = None
        self._resume_last_timestamp_ms = None
        self._reconnect_warmup_pending = False

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
        if self._resume_bucket is not None:
            # A reconnect may replay an older event from the same minute.
            # Do not let it rewind the stream or trigger a paper exit at an
            # obsolete price. Keep the resume check pending until a newer tick.
            if (self._resume_last_timestamp_ms is not None
                    and timestamp <= self._resume_last_timestamp_ms):
                # Without a sequence ID, an equal-timestamp replay cannot be
                # distinguished from a new trade in the same millisecond.
                # Reject it conservatively so reconnect replay cannot double-count.
                self.rejected += 1
                return {"accepted": False, "reason": "out_of_order"}
            # A reconnect that resumes in the same minute or the immediately
            # following minute is not a missing-candle gap. The interrupted
            # forming candle is discarded, but older fully closed candles remain
            # valid. Only one or more completely skipped minute buckets invalidate
            # the retained indicator history.
            if bucket > self._resume_bucket + self.interval_ms:
                self.closed.clear()
                self.gaps += 1
                gap_detected = True
                self._reconnect_warmup_pending = False
            else:
                # Continuity is confirmed, but readiness stays blocked until
                # one complete post-reconnect candle has actually closed.
                pass
            self._resume_bucket = None
            self._resume_last_timestamp_ms = None
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
                self.closed.append(self._as_row(self.current))
                # A reconnect is not warm again until one complete post-reconnect
                # candle has actually been closed and retained.
                self._reconnect_warmup_pending = False
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

    def restore_closed_candles(self, rows):
        """Seed indicators from a verified contiguous cache, pending live continuity."""
        if not isinstance(rows, (list, tuple)) or len(rows) < 5:
            raise ValueError("at least 5 closed candles are required")
        previous = None
        normalized = []
        for row in rows:
            if not isinstance(row, (list, tuple)) or len(row) != 6:
                raise ValueError("cached candle must have six OHLCV fields")
            stamp = row[0]
            if isinstance(stamp, bool) or not isinstance(stamp, int):
                raise ValueError("cached candle timestamp must be an integer")
            if stamp <= 0 or stamp % self.interval_ms:
                raise ValueError("cached candle timestamp is not interval-aligned")
            if previous is not None and stamp - previous != self.interval_ms:
                raise ValueError("cached candle history contains a gap")
            previous = stamp
            normalized.append(list(row))
        self.closed.clear()
        self.closed.extend(normalized[-self.closed.maxlen:])
        self.current = None
        self.last_timestamp_ms = None
        self.last_sequence = None
        # The first minute after restoring a cache may be only partially
        # observed; discard it and require a complete fresh minute to close.
        self.discard_next_close = True
        self._resume_bucket = self.closed[-1][0]
        self._resume_last_timestamp_ms = self._resume_bucket + self.interval_ms - 1
        self._reconnect_warmup_pending = True
        return len(self.closed)

    def reset_after_reconnect(self):
        """Retain closed candles only when the stream resumes in the same minute."""
        self._resume_bucket = self.current["open_time"] if self.current is not None else None
        self._resume_last_timestamp_ms = self.last_timestamp_ms
        self._reconnect_warmup_pending = self._resume_bucket is not None
        if self._resume_bucket is None:
            self.closed.clear()
        self.current = None
        self.last_timestamp_ms = None
        self.last_sequence = None
        self.discard_next_close = True

    def reset_after_gap(self):
        """Discard all history after a confirmed data gap."""
        self.closed.clear()
        self.current = None
        self.last_timestamp_ms = None
        self.last_sequence = None
        self._resume_bucket = None
        self._resume_last_timestamp_ms = None
        self._reconnect_warmup_pending = False
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
        return (len(self.closed) >= 20 and self.current is not None
                and not self.discard_next_close and not self._reconnect_warmup_pending)

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
                "reconnect_check_pending": self._resume_bucket is not None,
                "reconnect_warmup_pending": self._reconnect_warmup_pending,
                "rejected_trades": self.rejected}
