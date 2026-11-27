import os
import tempfile
import unittest

from candle_archive import (
    count_closed_candles,
    load_latest_contiguous_candles,
    record_closed_candles,
)
from market_candles import TradeCandleBuilder


class CandleProcessLifecycleTests(unittest.TestCase):
    def rows(self, start, count):
        return [[minute * 60_000, 100, 102, 99, 101, 4]
                for minute in range(start, start + count)]

    def trade(self, minute, second=10):
        return {
            "symbol": "BTC_USDT",
            "price": 101.0,
            "quantity": 1.0,
            "timestamp_ms": minute * 60_000 + second * 1000,
        }

    def test_archive_survives_full_app_restart_and_analysis_gap(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "closed-candles.sqlite3")
            original = self.rows(100, 30)
            self.assertEqual(record_closed_candles(path, "BTC_USDT", original), 30)

            # Simulate a completely new Android process: no old Python builder
            # or in-memory deque survives, only the app-private SQLite archive.
            restored = load_latest_contiguous_candles(
                path, "BTC_USDT", now_ms=140 * 60_000, limit=2000
            )
            self.assertEqual(len(restored), 30)
            builder = TradeCandleBuilder("BTC_USDT", max_candles=2000)
            self.assertEqual(builder.restore_closed_candles(restored), 30)

            # The app was not collecting while closed. A later live tick proves
            # a real time gap, so analysis warm-up must reset rather than bridge it.
            result = builder.add_trade(self.trade(140))
            self.assertTrue(result["accepted"])
            self.assertTrue(result["gap_detected"])
            self.assertEqual(builder.status()["closed_candles"], 0)

            # Resetting the analysis window must never delete historical records.
            self.assertEqual(count_closed_candles(path, "BTC_USDT"), 30)
            self.assertEqual(record_closed_candles(path, "BTC_USDT", original), 30)

    def test_restart_without_new_closed_rows_keeps_archive_count(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "closed-candles.sqlite3")
            rows = self.rows(200, 12)
            record_closed_candles(path, "BTC_USDT", rows)
            builder = TradeCandleBuilder("BTC_USDT", max_candles=2000)
            builder.restore_closed_candles(
                load_latest_contiguous_candles(
                    path, "BTC_USDT", now_ms=213 * 60_000, limit=2000
                )
            )
            # Repeated pause/stop flushes are idempotent.
            record_closed_candles(path, "BTC_USDT", list(builder.closed))
            record_closed_candles(path, "BTC_USDT", list(builder.closed))
            self.assertEqual(count_closed_candles(path, "BTC_USDT"), 12)


if __name__ == "__main__":
    unittest.main()
