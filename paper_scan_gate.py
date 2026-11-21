"""Freshness and cadence gate for the foreground paper-signal scheduler.

A stale snapshot invalidates the cadence anchor. The first fresh snapshot after
recovery is evaluated immediately; subsequent scans remain 15 seconds apart.
"""
import math


class PaperScanGate:
    def __init__(self, interval_seconds=15.0, max_snapshot_age_seconds=30.0):
        self.interval_seconds = self._positive(interval_seconds, "interval_seconds")
        self.max_snapshot_age_seconds = self._positive(
            max_snapshot_age_seconds, "max_snapshot_age_seconds"
        )
        self.last_scan = None

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

    def evaluate(self, now, received_at, snapshot_available=True):
        """Return (allowed, reason, age); stale/missing data never reaches engine."""
        if not snapshot_available or received_at is None:
            return False, "waiting_for_snapshot", None
        try:
            now_value = float(now)
            received_value = float(received_at)
        except (TypeError, ValueError, OverflowError):
            self.last_scan = None
            return False, "invalid_timestamp", None
        if not math.isfinite(now_value) or not math.isfinite(received_value):
            self.last_scan = None
            return False, "invalid_timestamp", None
        age = now_value - received_value
        if age < 0 or age > self.max_snapshot_age_seconds:
            # Do not make a recovered stream wait out the old cadence window.
            self.last_scan = None
            return False, "stale_snapshot", age
        if self.last_scan is not None and now_value - self.last_scan < self.interval_seconds:
            return False, "interval_not_elapsed", age
        return True, "ready", age

    def mark_scanned(self, now):
        value = self._positive(now, "scan_time")
        self.last_scan = value
