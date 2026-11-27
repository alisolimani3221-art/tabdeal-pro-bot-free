import unittest

from trade_stream_watchdog import TradeStreamWatchdog, reconnect_backoff_seconds


class FakeClock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class TradeStreamWatchdogTests(unittest.TestCase):
    def test_reconnect_backoff_grows_and_is_bounded(self):
        self.assertEqual([reconnect_backoff_seconds(n) for n in range(1, 7)],
                         [2, 4, 8, 16, 30, 30])

    def test_reconnect_backoff_rejects_invalid_attempts(self):
        for value in (True, 0, -1, 1.5, "2"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    reconnect_backoff_seconds(value)

    def test_unconnected_socket_is_not_mistaken_for_silent_connected_stream(self):
        clock = FakeClock()
        watchdog = TradeStreamWatchdog(clock=clock)
        self.assertFalse(watchdog.is_stale())
        self.assertIsNone(watchdog.seconds_since_valid_trade())

    def test_connected_stream_expires_after_silence_threshold(self):
        clock = FakeClock()
        watchdog = TradeStreamWatchdog(silence_timeout=45, clock=clock)
        watchdog.connected()
        clock.advance(44.9)
        self.assertFalse(watchdog.is_stale())
        clock.advance(0.1)
        self.assertTrue(watchdog.is_stale())
        self.assertAlmostEqual(watchdog.seconds_since_valid_trade(), 45.0)

    def test_only_a_new_valid_trade_refreshes_freshness(self):
        clock = FakeClock()
        watchdog = TradeStreamWatchdog(silence_timeout=45, clock=clock)
        watchdog.connected()
        clock.advance(40)
        watchdog.mark_valid_trade()
        clock.advance(40)
        self.assertFalse(watchdog.is_stale())
        self.assertAlmostEqual(watchdog.seconds_since_valid_trade(), 40.0)

    def test_invalid_timeouts_are_rejected(self):
        for value in (True, 0, -1, float("inf"), float("nan"), "bad"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    TradeStreamWatchdog(silence_timeout=value)


if __name__ == "__main__":
    unittest.main()
