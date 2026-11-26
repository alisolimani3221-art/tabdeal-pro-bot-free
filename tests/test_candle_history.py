import json
import os
import tempfile
import unittest

from candle_history import (
    validate_closed_candles, save_candle_cache, load_candle_cache,
    archive_candle_cache, warm_symbols,
)


class CandleHistoryTests(unittest.TestCase):
    def rows(self, start=0, count=24):
        return [[(start+i)*60000, 100, 102, 99, 101, 3] for i in range(count)]

    def test_filters_forming_candle_and_keeps_contiguous_closed_history(self):
        now = 30*60000 + 1000
        rows = self.rows(1, 25)
        rows.append([30*60000, 101, 103, 100, 102, 4])
        valid = validate_closed_candles(rows, now)
        self.assertEqual(len(valid), 25)
        self.assertLess(valid[-1][0], 30*60000)

    def test_keeps_latest_contiguous_suffix_after_gap_and_rejects_bad_ohlc(self):
        now = 40*60000
        valid = validate_closed_candles(
            self.rows(1, 10) + self.rows(12, 10), now
        )
        self.assertEqual(len(valid), 10)
        self.assertEqual(valid[0][0], 12*60000)
        self.assertEqual(valid[-1][0], 21*60000)
        bad = self.rows(1, 2)
        bad[0] = [60000, 100, 90, 95, 101, 1]
        with self.assertRaisesRegex(ValueError, "OHLC"):
            validate_closed_candles(bad, now)

    def test_atomic_cache_round_trip_and_symbol_binding(self):
        now = 40*60000
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "BTC_USDT.json")
            rows = validate_closed_candles(self.rows(1, 24), now)
            save_candle_cache(path, "BTC_USDT", rows)
            self.assertEqual(load_candle_cache(path, "BTC_USDT", now), rows)
            with self.assertRaisesRegex(ValueError, "symbol mismatch"):
                load_candle_cache(path, "ETH_USDT", now)

    def test_gap_archive_preserves_invalidated_cache_without_active_reuse(self):
        now = 40 * 60000
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "BTC_USDT.json")
            rows = validate_closed_candles(self.rows(1, 3), now)
            save_candle_cache(path, "BTC_USDT", rows)
            archive = archive_candle_cache(path, "confirmed_gap", now)
            self.assertIsNotNone(archive)
            self.assertTrue(os.path.isfile(archive))
            self.assertFalse(os.path.exists(path))
            self.assertEqual(load_candle_cache(archive, "BTC_USDT", now), rows)
            self.assertIsNone(archive_candle_cache(path, "confirmed_gap", now))

    def test_parallel_warmup_isolates_symbol_failure(self):
        def fetch(symbol):
            if symbol == "BAD_USDT":
                raise OSError("offline")
            return self.rows(1, 24)
        result = warm_symbols(["BTC_USDT", "ETH_USDT", "BAD_USDT"],
                              fetch, 40*60000, max_workers=2)
        self.assertEqual(result["requested"], 3)
        self.assertEqual(result["ready_count"], 2)
        self.assertIn("BAD_USDT", result["errors"])
        self.assertFalse(result["live_orders"])

    def test_rejects_nonfinite_and_boolean_values(self):
        bad = self.rows(1, 2)
        bad[0][1] = float("nan")
        with self.assertRaises(ValueError):
            validate_closed_candles(bad, 10*60000)
        bad = self.rows(1, 2)
        bad[0][1] = True
        with self.assertRaises(ValueError):
            validate_closed_candles(bad, 10*60000)


if __name__ == "__main__":
    unittest.main()
