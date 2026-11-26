import os
import tempfile
import unittest

from candle_archive import CandleArchive, CandleArchiveError
from market_candles import TradeCandleBuilder


class CandleArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.temp.name, "candles.sqlite3")
        self.archive = CandleArchive(self.path, max_rows=100)

    def tearDown(self):
        self.temp.cleanup()

    def row(self, minute, price=100.0):
        return [minute * 60000, price, price + 2, price - 2, price + 1, 3.5]

    def test_round_trip_survives_new_archive_instance(self):
        self.archive.save_closed("BTC_USDT", 60000, self.row(100))
        reopened = CandleArchive(self.path, max_rows=100)
        self.assertEqual(reopened.load_recent("BTC_USDT", 60000), [self.row(100)])

    def test_duplicate_write_is_idempotent_and_conflict_fails(self):
        self.assertTrue(self.archive.save_closed("BTC_USDT", 60000, self.row(100)))
        self.assertFalse(self.archive.save_closed("BTC_USDT", 60000, self.row(100)))
        with self.assertRaises(CandleArchiveError):
            self.archive.save_closed("BTC_USDT", 60000, self.row(100, 101))

    def test_symbols_and_intervals_are_isolated(self):
        self.archive.save_closed("BTC_USDT", 60000, self.row(100))
        self.archive.save_closed("ETH_USDT", 60000, self.row(100))
        self.archive.save_closed("BTC_USDT", 300000, self.row(100))
        self.assertEqual(len(self.archive.load_recent("BTC_USDT", 60000)), 1)
        self.assertEqual(len(self.archive.load_recent("ETH_USDT", 60000)), 1)
        self.assertEqual(len(self.archive.load_recent("BTC_USDT", 300000)), 1)

    def test_invalid_non_finite_and_inconsistent_rows_are_rejected(self):
        for row in ([6000000, 100, 99, 98, 100, 1],
                    [6000000, 100, 105, 101, 104, 1],
                    [6000000, 100, float("nan"), 99, 101, 1]):
            with self.subTest(row=row):
                with self.assertRaises(ValueError):
                    self.archive.save_closed("BTC_USDT", 60000, row)

    def test_builder_restores_archive_but_rebuilds_contiguous_warmup(self):
        builder = TradeCandleBuilder("BTC_USDT", archive=self.archive)
        def trade(minute):
            return {"symbol": "BTC_USDT", "price": 100, "quantity": 1,
                    "timestamp_ms": minute * 60000 + 1000}
        builder.add_trade(trade(100))
        builder.add_trade(trade(101))
        builder.add_trade(trade(102))
        self.assertEqual(builder.status()["stored_closed_candles"], 1)
        restored = TradeCandleBuilder("BTC_USDT", archive=CandleArchive(self.path))
        self.assertEqual(restored.status()["stored_closed_candles"], 1)
        self.assertEqual(restored.status()["closed_candles"], 0)
        self.assertFalse(restored.is_ready)


if __name__ == "__main__":
    unittest.main()
