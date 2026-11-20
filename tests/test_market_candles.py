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

    def test_short_reconnect_preserves_closed_history_but_discards_partial_candle(self):
        builder = TradeCandleBuilder("BTC_USDT")
        for minute in range(100, 153):
            builder.add_trade(self.trade(minute))
        self.assertTrue(builder.is_ready)
        retained = len(builder.closed)
        forming_minute = builder.current["open_time"] // 60_000

        builder.reset_after_reconnect()
        self.assertEqual(len(builder.closed), retained)
        self.assertIsNone(builder.current)
        self.assertFalse(builder.is_ready)

        resumed = builder.add_trade(self.trade(forming_minute, second=20))
        self.assertTrue(resumed["accepted"])
        self.assertFalse(resumed["gap_detected"])
        self.assertEqual(len(builder.closed), retained)
        self.assertFalse(builder.is_ready)

        partial_close = builder.add_trade(self.trade(forming_minute + 1))
        self.assertFalse(partial_close["ready"])
        self.assertFalse(builder.is_ready)
        self.assertEqual(len(builder.closed), retained)

        full_close = builder.add_trade(self.trade(forming_minute + 2))
        self.assertTrue(full_close["ready"])
        self.assertTrue(builder.is_ready)
        self.assertEqual(len(builder.closed), retained + 1)

    def test_reconnect_after_minute_boundary_invalidates_retained_history(self):
        builder = TradeCandleBuilder("BTC_USDT")
        for minute in range(100, 153):
            builder.add_trade(self.trade(minute))
        self.assertTrue(builder.is_ready)
        builder.reset_after_reconnect()

        result = builder.add_trade(self.trade(153))
        self.assertTrue(result["accepted"])
        self.assertTrue(result["gap_detected"])
        self.assertEqual(len(builder.closed), 0)
        self.assertFalse(builder.is_ready)

    def test_hard_gap_reset_discards_all_warmup_data(self):
        builder = TradeCandleBuilder("BTC_USDT")
        builder.add_trade(self.trade(100))
        builder.add_trade(self.trade(101))
        self.assertEqual(len(builder.closed), 0)
        builder.reset_after_gap()
        self.assertEqual(len(builder.closed), 0)
        self.assertIsNone(builder.current)
        self.assertFalse(builder.is_ready)

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
