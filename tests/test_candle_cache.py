import json
import os
import tempfile
import unittest

from candle_cache import (
    clear_candle_cache,
    load_candle_cache,
    save_candle_cache,
)


class CandleCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.temp.name, "candles.json")
        self.interval = 60_000
        self.now = 200 * self.interval + 12_000
        self.rows = [
            [minute * self.interval, 100.0, 101.0, 99.0, 100.5, 12.0]
            for minute in range(149, 200)
        ]

    def tearDown(self):
        self.temp.cleanup()

    def test_round_trip_restores_only_contiguous_recent_history(self):
        self.assertTrue(save_candle_cache(
            self.path, "BTC_USDT", self.rows, saved_at_ms=self.now
        ))
        restored = load_candle_cache(
            self.path, "BTC_USDT", now_ms=self.now + 10_000
        )
        self.assertEqual(restored, self.rows)

    def test_rejects_cache_after_current_minute_has_advanced(self):
        self.assertTrue(save_candle_cache(
            self.path, "BTC_USDT", self.rows, saved_at_ms=self.now
        ))
        self.assertIsNone(load_candle_cache(
            self.path, "BTC_USDT", now_ms=self.now + self.interval
        ))

    def test_rejects_stale_or_future_saved_time(self):
        self.assertTrue(save_candle_cache(
            self.path, "BTC_USDT", self.rows, saved_at_ms=self.now
        ))
        self.assertIsNone(load_candle_cache(
            self.path, "BTC_USDT", now_ms=self.now + 60_001
        ))
        self.assertIsNone(load_candle_cache(
            self.path, "BTC_USDT", now_ms=self.now - 1
        ))

    def test_rejects_symbol_mismatch_and_discontinuous_history(self):
        self.assertTrue(save_candle_cache(
            self.path, "BTC_USDT", self.rows, saved_at_ms=self.now
        ))
        self.assertIsNone(load_candle_cache(
            self.path, "ETH_USDT", now_ms=self.now
        ))
        broken = list(self.rows)
        broken[20] = list(broken[20])
        broken[20][0] += self.interval
        self.assertFalse(save_candle_cache(
            self.path, "BTC_USDT", broken, saved_at_ms=self.now
        ))

    def test_rejects_cache_with_invalid_ohlcv(self):
        broken = [list(row) for row in self.rows]
        broken[-1][2] = 1
        self.assertFalse(save_candle_cache(
            self.path, "BTC_USDT", broken, saved_at_ms=self.now
        ))

    def test_clear_cache_is_idempotent(self):
        self.assertTrue(save_candle_cache(
            self.path, "BTC_USDT", self.rows, saved_at_ms=self.now
        ))
        clear_candle_cache(self.path)
        clear_candle_cache(self.path)
        self.assertFalse(os.path.exists(self.path))


if __name__ == "__main__":
    unittest.main()
