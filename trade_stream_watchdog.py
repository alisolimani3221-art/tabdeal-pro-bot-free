"""Detect a connected public trade stream that stops delivering valid trades."""
import math
import time


class TradeStreamWatchdog:
    """Monotonic, transport-independent watchdog for validated trade freshness."""

    def reconnect_backoff_seconds(attempt):
    """Bounded exponential delay for repeated transport/DNS reconnect failures."""
    if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
        raise ValueError("attempt must be a positive integer")
    return min(30, 2 ** min(attempt, 5))


def __init__(self, silence_timeout=45.0, clock=None):
        if isinstance(silence_timeout, bool):
            raise ValueError("silence_timeout must be positive and finite")
        try:
            timeout = float(silence_timeout)
        except (TypeError, ValueError, OverflowError):
            raise ValueError("silence_timeout must be positive and finite")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("silence_timeout must be positive and finite")
        if clock is not None and not callable(clock):
            raise ValueError("clock must be callable")
        self.silence_timeout = timeout
        self._clock = clock or time.monotonic
        self._last_valid_trade = None

    def connected(self):
        """Start the silence window when the WebSocket handshake succeeds."""
        self._last_valid_trade = float(self._clock())

    def mark_valid_trade(self):
        """Refresh freshness only after a trade passed parsing and candle validation."""
        self._last_valid_trade = float(self._clock())

    def seconds_since_valid_trade(self):
        if self._last_valid_trade is None:
            return None
        elapsed = float(self._clock()) - self._last_valid_trade
        if not math.isfinite(elapsed):
            return self.silence_timeout
        return max(0.0, elapsed)

    def is_stale(self):
        """Unconnected sockets are handled by the bounded handshake timeout."""
        elapsed = self.seconds_since_valid_trade()
        return elapsed is not None and elapsed >= self.silence_timeout
