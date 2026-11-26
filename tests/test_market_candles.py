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

    def test_gap_preserves_archived_closed_candles_but_resets_warmup(self):
        builder = TradeCandleBuilder("BTC_USDT")
        builder.add_trade(self.trade(100))  # partial first bucket
        builder.add_trade(self.trade(101))
        builder.add_trade(self.trade(102))  # closes minute 101
        self.assertEqual(builder.status()["stored_closed_candles"], 1)
        result = builder.add_trade(self.trade(104))  # missing minute 103
        self.assertTrue(result["gap_detected"])
        self.assertEqual(builder.status()["closed_candles"], 0)
        self.assertEqual(builder.status()["stored_closed_candles"], 2)
        self.assertFalse(builder.is_ready)

    def test_clock_finalizes_elapsed_candle_without_following_trade(self):
        builder = TradeCandleBuilder("BTC_USDT")
        builder.add_trade(self.trade(100))  # partial first bucket
        builder.add_trade(self.trade(101))  # closes/discards minute 100
        self.assertTrue(builder.finalize_due(102 * 60_000))
        self.assertIsNone(builder.current)
        self.assertEqual(builder.status()["closed_candles"], 1)
        self.assertEqual(builder.candles_for_analysis()[0][0], 101 * 60_000)
        self.assertFalse(builder.is_ready)

    def test_clock_finalization_detects_gap_and_rejects_late_trade(self):
        builder = TradeCandleBuilder("BTC_USDT")
        builder.add_trade(self.trade(100))
        builder.add_trade(self.trade(101))
        builder.finalize_due(102 * 60_000)
        late = builder.add_trade(self.trade(101, second=50))
        self.assertEqual(late["reason"], "late_trade_for_closed_candle")
        resumed = builder.add_trade(self.trade(103))
        self.assertTrue(resumed["gap_detected"])
        self.assertEqual(builder.status()["closed_candles"], 0)
        self.assertEqual(builder.status()["gaps"], 1)

    def test_clock_finalization_persists_to_archive(self):
        import os
        import tempfile
        from candle_archive import CandleArchive
        with tempfile.TemporaryDirectory() as directory:
            archive = CandleArchive(os.path.join(directory, "candles.sqlite3"))
            builder = TradeCandleBuilder("BTC_USDT", archive=archive)
            builder.add_trade(self.trade(100))
            builder.add_trade(self.trade(101))
            self.assertTrue(builder.finalize_due(102 * 60_000))
            self.assertEqual(builder.status()["stored_closed_candles"], 1)
            restored = TradeCandleBuilder(
                "BTC_USDT", archive=CandleArchive(os.path.join(directory, "candles.sqlite3"))
            )
            self.assertEqual(restored.status()["stored_closed_candles"], 1)

    def test_reconnect_preserves_archived_closed_candles(self):
        builder = TradeCandleBuilder("BTC_USDT")
        builder.add_trade(self.trade(100))
        builder.add_trade(self.trade(101))
        builder.add_trade(self.trade(102))
        self.assertEqual(builder.status()["stored_closed_candles"], 1)
        builder.reset_after_gap()
        self.assertEqual(builder.status()["closed_candles"], 0)
        self.assertEqual(builder.status()["stored_closed_candles"], 1)
        self.assertIsNone(builder.current)
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

    def test_archive_write_failure_invalidates_indicator_warmup(self):
        class BrokenArchive:
            def load_recent(self, symbol, interval_ms, limit):
                return []

            def save_closed(self, symbol, interval_ms, row):
                raise OSError("simulated disk failure")

        builder = TradeCandleBuilder("BTC_USDT", archive=BrokenArchive())
        for minute in range(100, 154):
            builder.add_trade(self.trade(minute))
        self.assertFalse(builder.is_ready)
        self.assertEqual(len(builder.closed), 0)
        self.assertEqual(builder.status()["archive_error"], "OSError")

    def test_stream_heartbeat_requires_connected_recent_monotonic_pong(self):
        from market_candles import stream_heartbeat_is_fresh
        self.assertTrue(stream_heartbeat_is_fresh(True, 100.0, 120.0))
        self.assertFalse(stream_heartbeat_is_fresh(False, 100.0, 101.0))
        self.assertFalse(stream_heartbeat_is_fresh(True, None, 101.0))
        self.assertFalse(stream_heartbeat_is_fresh(True, 60.0, 100.1))
        self.assertFalse(stream_heartbeat_is_fresh(True, 101.0, 100.0))
        self.assertFalse(stream_heartbeat_is_fresh(True, float("nan"), 101.0))

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
