import os
import tempfile
import unittest

from candle_archive import record_closed_candles, count_closed_candles


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


if __name__ == "__main__":
    unittest.main()
