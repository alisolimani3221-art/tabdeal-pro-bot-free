import os
import tempfile
import unittest

from candle_archive import (record_closed_candles, count_closed_candles,
                            load_latest_contiguous_candles)


class DurableCandleArchiveTests(unittest.TestCase):
    def rows(self, start, count):
        return [[minute * 60_000, 100, 102, 99, 101, 4]
                for minute in range(start, start + count)]

    def test_archive_survives_reopen_and_gap_without_becoming_warmup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "candles.sqlite3")
            self.assertEqual(record_closed_candles(path, "BTC_USDT", self.rows(1, 4)), 4)
            # Simulate process restart and a fresh contiguous run after a gap.
            self.assertEqual(count_closed_candles(path, "BTC_USDT"), 4)
            self.assertEqual(record_closed_candles(path, "BTC_USDT", self.rows(8, 2)), 6)
            self.assertEqual(count_closed_candles(path, "BTC_USDT"), 6)

    def test_repeated_snapshots_are_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "candles.sqlite3")
            rows = self.rows(10, 5)
            self.assertEqual(record_closed_candles(path, "BTC_USDT", rows), 5)
            self.assertEqual(record_closed_candles(path, "BTC_USDT", rows), 5)

    def test_rejects_invalid_and_partial_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "candles.sqlite3")
            with self.assertRaises(ValueError):
                record_closed_candles(path, "BTC_USDT", [[60_000, 1, 1, 1, 1]])
            bad = self.rows(1, 1)
            bad[0][2] = 90
            with self.assertRaises(ValueError):
                record_closed_candles(path, "BTC_USDT", bad)


    def test_loads_only_latest_contiguous_closed_run(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "candles.sqlite3")
            rows = self.rows(1, 4) + self.rows(8, 3)
            record_closed_candles(path, "BTC_USDT", rows)
            # Current in-progress minute must not be restored as a closed candle.
            current = self.rows(12, 1)
            record_closed_candles(path, "BTC_USDT", current)
            loaded = load_latest_contiguous_candles(
                path, "BTC_USDT", now_ms=12 * 60_000 + 1000, limit=20
            )
            self.assertEqual([row[0] for row in loaded],
                             [8*60_000, 9*60_000, 10*60_000])
            self.assertEqual(count_closed_candles(path, "BTC_USDT"), 8)

    def test_archive_restore_empty_for_missing_or_wrong_symbol(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "candles.sqlite3")
            self.assertEqual(load_latest_contiguous_candles(
                path, "BTC_USDT", now_ms=10*60_000
            ), [])
            record_closed_candles(path, "BTC_USDT", self.rows(1, 3))
            self.assertEqual(load_latest_contiguous_candles(
                path, "ETH_USDT", now_ms=10*60_000
            ), [])


if __name__ == "__main__":
    unittest.main()
