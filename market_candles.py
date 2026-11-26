"""Conservative one-minute candle builder for normalized Tabdeal trade events.

Only validated last-trade events belong here. Order-book prices must never be
passed as trades. A timestamp gap or out-of-order event invalidates warm-up so
technical indicators cannot silently span missing market data.
"""
import math
import re
from collections import deque


MIN_MONITOR_CANDLES = 5
MIN_ANALYSIS_CANDLES = 23


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
        self.rejection_reasons = {}
        self.gaps = 0
        self.reconnect_count = 0
        self.partial_buckets_discarded = 0
        self.history_clear_count = 0
        self.last_reset_reason = "startup_partial_bucket_pending"
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

    def _reject(self, reason):
        self.rejected += 1
        self.rejection_reasons[reason] = self.rejection_reasons.get(reason, 0) + 1
        return {"accepted": False, "reason": reason}

    def add_trade(self, trade):
        """Add one normalized trade; return a compact acceptance diagnostic."""
        if not isinstance(trade, dict) or trade.get("ok") is False:
            return self._reject("invalid_trade")
        if str(trade.get("symbol", "")).upper() != self.symbol:
            return self._reject("symbol_mismatch")
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
            return self._reject("invalid_fields")

        if self.last_timestamp_ms is not None and timestamp < self.last_timestamp_ms:
            return self._reject("out_of_order")
        if sequence is not None and self.last_sequence is not None:
            if sequence == self.last_sequence:
                return self._reject("duplicate_sequence")
            if sequence < self.last_sequence:
                return self._reject("older_sequence")

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
                return self._reject("out_of_order")
            # A reconnect that resumes in the same minute or the immediately
            # following minute is not a missing-candle gap. The interrupted
            # forming candle is discarded, but older fully closed candles remain
            # valid. Only one or more completely skipped minute buckets invalidate
            # the retained indicator history.
            if bucket > self._resume_bucket + self.interval_ms:
                self.closed.clear()
                self.history_clear_count += 1
                self.last_reset_reason = "confirmed_reconnect_gap"
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
            return self._reject("out_of_order")
        elif bucket == self.current["open_time"]:
            if timestamp < self.current["last_timestamp"]:
                return self._reject("out_of_order")
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
                self.partial_buckets_discarded += 1
                self.last_reset_reason = "first_partial_bucket_discarded"
                # The first post-startup/reconnect bucket is intentionally
                # discarded because it was only partially observed. Retained
                # cached/pre-reconnect candles end before that missing bucket,
                # so keeping them would create a silent one-minute hole in the
                # next analysis window. Restart warm-up from fully observed live
                # candles instead of passing a discontinuous series to indicators.
                if self.closed:
                    # The cached/previous closed history ends before this
                    # partially observed bucket. Once that bucket closes, the
                    # next live bucket would otherwise leave a one-minute hole.
                    # Invalidate and archive the old cache rather than append
                    # an incomplete OHLCV row or silently bridge the gap.
                    self.closed.clear()
                    self.history_clear_count += 1
                    self.gaps += 1
                    gap_detected = True
                    self.last_reset_reason = "history_cleared_after_partial_bucket"
                # The reconnect warm-up gate ends when the first partial bucket
                # has been discarded, even when there was no closed history to clear.
                self._reconnect_warmup_pending = False
            if bucket != previous_bucket + self.interval_ms:
                if not gap_detected:
                    self.gaps += 1
                gap_detected = True
                # Do not let indicators bridge an unknown interval.
                self.closed.clear()
                self.history_clear_count += 1
                self.last_reset_reason = "timestamp_gap"
                self.discard_next_close = True
            self.current = self._new_candle(bucket, price, quantity, timestamp)

        self.last_timestamp_ms = timestamp
        if sequence is not None:
            self.last_sequence = sequence
        return {"accepted": True, "gap_detected": gap_detected,
                "closed_candles": len(self.closed), "ready": self.is_ready}

    def restore_closed_candles(self, rows):
        """Seed indicators from any verified contiguous cache, pending live continuity."""
        if not isinstance(rows, (list, tuple)) or len(rows) < 1:
            raise ValueError("at least 1 closed candle is required")
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
        # A cache contains only fully closed candles. If the first live trade
        # arrives in the immediately following minute, its forming candle is
        # valid alongside that history; do not discard it and erase the cache.
        # _resume_bucket enforces exact minute continuity before the cache can
        # be used. A skipped full minute still clears the active history.
        # The first live bucket after process startup is only partially
        # observed. It may be used as a forming candle (analysis excludes it),
        # but must be discarded at its close; never append it as full OHLCV.
        self.discard_next_close = True
        self._resume_bucket = self.closed[-1][0]
        self._resume_last_timestamp_ms = self._resume_bucket + self.interval_ms - 1
        self._reconnect_warmup_pending = False
        return len(self.closed)

    def reset_after_reconnect(self):
        """Retain closed candles only when the stream resumes in the same minute."""
        self.reconnect_count += 1
        self.last_reset_reason = "websocket_reconnect_pending_continuity_check"
        self._resume_bucket = self.current["open_time"] if self.current is not None else None
        self._resume_last_timestamp_ms = self.last_timestamp_ms
        self._reconnect_warmup_pending = self._resume_bucket is not None
        if self._resume_bucket is None:
            if self.closed:
                self.history_clear_count += 1
            self.closed.clear()
            self.last_reset_reason = "reconnect_without_forming_bucket"
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
        self.history_clear_count += 1
        self.last_reset_reason = "explicit_gap_reset"
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
    def monitor_ready(self):
        """Five complete candles permit early monitoring, never technical entry."""
        return len(self.closed) >= MIN_MONITOR_CANDLES

    def preliminary_trend(self):
        """Summarize the last five closed candles without creating a trade signal."""
        if not self.monitor_ready:
            return None
        rows = list(self.closed)[-MIN_MONITOR_CANDLES:]
        first_close = float(rows[0][4])
        last_close = float(rows[-1][4])
        change_pct = (last_close / first_close - 1.0) * 100.0
        if not math.isfinite(change_pct):
            return None
        direction = "UP" if change_pct > 0 else "DOWN" if change_pct < 0 else "FLAT"
        return {"trend": direction, "change_pct": change_pct,
                "candles": MIN_MONITOR_CANDLES, "signal": None,
                "trade_allowed": False}

    @property
    def is_ready(self):
        # Keep technical signals/entries gated until 23 closed candles and a
        # live forming candle are present; analyze_klines excludes that last row.
        return (len(self.closed) >= MIN_ANALYSIS_CANDLES and self.current is not None
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
                "monitor_ready": self.monitor_ready,
                "analysis_required_candles": MIN_ANALYSIS_CANDLES,
                "ready": self.is_ready, "gaps": self.gaps,
                "reconnect_check_pending": self._resume_bucket is not None,
                "reconnect_warmup_pending": self._reconnect_warmup_pending,
                "rejected_trades": self.rejected,
                "rejection_reasons": dict(self.rejection_reasons),
                "reconnect_count": self.reconnect_count,
                "partial_buckets_discarded": self.partial_buckets_discarded,
                "history_clear_count": self.history_clear_count,
                "last_reset_reason": self.last_reset_reason}
