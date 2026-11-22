import unittest

from market_candles import TradeCandleBuilder


class TradeCandleBuilderTests(unittest.TestCase):
    def trade(self, minute, second=1, price=100, quantity=2, sequence=None):
        value = {"symbol": "BTC_USDT", "price": price, "quantity": quantity,
                 "timestamp_ms": minute * 60_000 + second * 1000}
        if sequence is not None:
            value["sequence"] = sequence
        return value

    def test_builds_ohlcv_from_valid_trades(self):
        builder = TradeCandleBuilder("BTC_USDT")
        builder.add_trade(self.trade(99, 1, 99, 1, 1))  # partial first bucket
        builder.add_trade(self.trade(100, 1, 100, 2, 2))
        builder.add_trade(self.trade(100, 20, 105, 3, 3))
        builder.add_trade(self.trade(101, 1, 102, 4, 4))
        rows = builder.candles_for_analysis()
        self.assertEqual(rows[0], [6_000_000, 100.0, 105.0, 100.0, 105.0, 5.0])
        self.assertEqual(rows[1], [6_060_000, 102.0, 102.0, 102.0, 102.0, 4.0])

    def test_rejects_wrong_symbol_and_invalid_fields(self):
        builder = TradeCandleBuilder("BTC_USDT")
        self.assertEqual(builder.add_trade(self.trade(100) | {"symbol": "ETH_USDT"})["reason"],
                         "symbol_mismatch")
        for patch in ({"price": True}, {"quantity": 0}, {"timestamp_ms": -1}):
            self.assertFalse(builder.add_trade(self.trade(100) | patch)["accepted"])

    def test_rejects_out_of_order_and_duplicate_sequence(self):
        builder = TradeCandleBuilder("BTC_USDT")
        self.assertTrue(builder.add_trade(self.trade(100, 20, sequence=9))["accepted"])
        self.assertEqual(builder.add_trade(self.trade(100, 10, sequence=10))["reason"],
                         "out_of_order")
        self.assertEqual(builder.add_trade(self.trade(100, 30, sequence=9))["reason"],
                         "duplicate_or_old_sequence")

    def test_gap_clears_warmup_history(self):
        builder = TradeCandleBuilder("BTC_USDT")
        builder.add_trade(self.trade(100))
        result = builder.add_trade(self.trade(102))
        self.assertTrue(result["gap_detected"])
        self.assertEqual(builder.status()["closed_candles"], 0)
        self.assertEqual(builder.status()["gaps"], 1)
        self.assertFalse(builder.is_ready)

    def test_ready_requires_51_closed_and_one_forming_candle(self):
        builder = TradeCandleBuilder("BTC_USDT")
        for minute in range(100, 153):
            builder.add_trade(self.trade(minute))
        self.assertTrue(builder.is_ready)
        self.assertEqual(len(builder.candles_for_analysis()), 52)

    # Include 51 fully observed closed candles after discarding the first partial bucket.
    def test_gap_prevents_false_contiguous_readiness(self):
        builder = TradeCandleBuilder("BTC_USDT")
        for minute in range(100, 153):
            builder.add_trade(self.trade(minute))
        self.assertTrue(builder.is_ready)
        builder.add_trade(self.trade(154))
        self.assertFalse(builder.is_ready)
        self.assertEqual(len(builder.closed), 0)

    def test_reconnect_reset_discards_all_warmup_data(self):
        builder = TradeCandleBuilder("BTC_USDT")
        builder.add_trade(self.trade(100))
        builder.add_trade(self.trade(101))
        self.assertEqual(len(builder.closed), 0)
        builder.reset_after_gap()
        self.assertEqual(len(builder.closed), 0)
        self.assertIsNone(builder.current)
        self.assertFalse(builder.is_ready)

    def test_restored_history_waits_for_one_fully_observed_live_candle(self):
        builder = TradeCandleBuilder("BTC_USDT")
        rows = []
        for minute in range(100, 151):
            rows.append([minute * 60_000, 100, 101, 99, 100, 10])
        builder.restore_closed_candles(rows)
        self.assertFalse(builder.is_ready)
        first = builder.add_trade(self.trade(151, sequence=1))
        self.assertTrue(first["accepted"])
        self.assertFalse(builder.is_ready)
        second = builder.add_trade(self.trade(152, sequence=2))
        self.assertTrue(second["accepted"])
        self.assertTrue(builder.is_ready)
        self.assertEqual(len(builder.candles_for_analysis()), 52)
        self.assertEqual(builder.status()["closed_candles"], 51)

    def test_restored_history_is_discarded_if_first_live_minute_was_missed(self):
        builder = TradeCandleBuilder("BTC_USDT")
        rows = [[minute * 60_000, 100, 101, 99, 100, 10]
                for minute in range(100, 151)]
        builder.restore_closed_candles(rows)
        result = builder.add_trade(self.trade(152, sequence=1))
        self.assertTrue(result["accepted"])
        self.assertTrue(result["gap_detected"])
        self.assertEqual(builder.status()["closed_candles"], 0)
        self.assertFalse(builder.is_ready)

    def test_restored_history_rejects_a_trade_older_than_expected_bucket(self):
        builder = TradeCandleBuilder("BTC_USDT")
        rows = [[minute * 60_000, 100, 101, 99, 100, 10]
                for minute in range(100, 151)]
        builder.restore_closed_candles(rows)
        result = builder.add_trade(self.trade(150, sequence=1))
        self.assertFalse(result["accepted"])
        self.assertEqual(result["reason"], "before_cached_history")

    def test_invalid_builder_configuration(self):
        for symbol in ("BTCUSDT", "BTC/USDT", "BTC__USDT", ""):
            with self.subTest(symbol=symbol):
                with self.assertRaises(ValueError):
                    TradeCandleBuilder(symbol)
        with self.assertRaises(ValueError):
            TradeCandleBuilder("BTC_USDT", interval_ms=0)
        with self.assertRaises(ValueError):
            TradeCandleBuilder("BTC_USDT", max_candles=10)


if __name__ == "__main__":
    unittest.main()
