import unittest

from market_candles import TradeCandleBuilder
from risk_analysis import analyze_klines


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
                         "duplicate_sequence")

    def test_gap_clears_warmup_history(self):
        builder = TradeCandleBuilder("BTC_USDT")
        builder.add_trade(self.trade(100))
        result = builder.add_trade(self.trade(102))
        self.assertTrue(result["gap_detected"])
        self.assertEqual(builder.status()["closed_candles"], 0)
        self.assertEqual(builder.status()["gaps"], 1)
        self.assertFalse(builder.is_ready)

    def test_five_closed_candles_enable_monitoring_but_not_analysis(self):
        builder = TradeCandleBuilder("BTC_USDT")
        for minute in range(100, 107):
            builder.add_trade(self.trade(minute))
        self.assertTrue(builder.monitor_ready)
        self.assertFalse(builder.is_ready)
        self.assertEqual(builder.status()["analysis_required_candles"], 23)

    def test_preliminary_trend_after_five_candles_is_not_a_trade_signal(self):
        builder = TradeCandleBuilder("BTC_USDT")
        self.assertIsNone(builder.preliminary_trend())
        for minute in range(100, 107):
            builder.add_trade(self.trade(minute, price=100 + minute))
        trend = builder.preliminary_trend()
        self.assertEqual(trend["trend"], "UP")
        self.assertEqual(trend["candles"], 5)
        self.assertIsNone(trend["signal"])
        self.assertFalse(trend["trade_allowed"])
        self.assertFalse(builder.is_ready)

    def test_analysis_requires_23_closed_and_one_forming_candle(self):
        builder = TradeCandleBuilder("BTC_USDT")
        for minute in range(100, 125):
            builder.add_trade(self.trade(minute))
        self.assertTrue(builder.is_ready)
        self.assertEqual(len(builder.candles_for_analysis()), 24)

    # Include 23 fully observed closed candles after discarding the first partial bucket.
    def test_gap_prevents_false_contiguous_readiness(self):
        builder = TradeCandleBuilder("BTC_USDT")
        for minute in range(100, 125):
            builder.add_trade(self.trade(minute))
        self.assertTrue(builder.is_ready)
        builder.add_trade(self.trade(126))
        self.assertFalse(builder.is_ready)
        self.assertEqual(len(builder.closed), 0)

    def test_short_reconnect_retains_history_but_waits_for_one_full_new_candle(self):
        builder = TradeCandleBuilder("BTC_USDT")
        for minute in range(100, 125):
            builder.add_trade(self.trade(minute))
        self.assertTrue(builder.is_ready)
        forming_minute = builder.current["open_time"] // 60_000
        original_closed = len(builder.closed)

        builder.reset_after_reconnect()
        resumed = builder.add_trade(self.trade(forming_minute, second=20))
        self.assertTrue(resumed["accepted"])
        self.assertFalse(resumed["gap_detected"])
        self.assertFalse(builder.is_ready)

        # The interrupted minute is discarded, but verified older closed bars
        # remain. The engine stays gated until a fully observed minute closes.
        builder.add_trade(self.trade(forming_minute + 1))
        self.assertEqual(len(builder.closed), original_closed)
        self.assertFalse(builder.is_ready)
        builder.add_trade(self.trade(forming_minute + 2))
        self.assertEqual(len(builder.closed), original_closed + 1)
        self.assertTrue(builder.is_ready)
        self.assertEqual(builder.status()["history_clear_count"], 0)

    def test_reconnect_after_finalized_candle_preserves_history_until_continuity_check(self):
        builder = TradeCandleBuilder("BTC_USDT")
        for minute in range(100, 126):
            builder.add_trade(self.trade(minute))
        self.assertEqual(len(builder.closed), 24)
        # Finalize the latest forming candle so the disconnect occurs between buckets.
        finalized = builder.finalize_due(127 * 60_000 + 30_000)
        self.assertTrue(finalized["finalized"])
        self.assertIsNone(builder.current)
        self.assertEqual(len(builder.closed), 25)

        builder.reset_after_reconnect()
        self.assertEqual(len(builder.closed), 25)
        self.assertEqual(
            builder.status()["last_reset_reason"],
            "reconnect_waiting_for_adjacent_bucket",
        )

        # An adjacent live bucket keeps the validated closed history available
        # while it forms; the forming candle itself is not included in analysis.
        resumed = builder.add_trade(self.trade(126, second=20))
        self.assertTrue(resumed["accepted"])
        self.assertFalse(resumed["gap_detected"])
        self.assertEqual(len(builder.closed), 25)
        self.assertFalse(builder.is_ready)

        # The reconnect bucket is partial and is dropped, but the old closed
        # history is retained. Analysis resumes after the next full minute closes.
        rollover = builder.add_trade(self.trade(127, second=2))
        self.assertFalse(rollover["gap_detected"])
        self.assertEqual(len(builder.closed), 25)
        self.assertFalse(builder.is_ready)
        builder.add_trade(self.trade(128, second=2))
        self.assertEqual(len(builder.closed), 26)
        self.assertTrue(builder.is_ready)

    def test_reconnect_with_no_closed_history_does_not_stick_warmup_gate(self):
        builder = TradeCandleBuilder("BTC_USDT")
        builder.add_trade(self.trade(100))
        self.assertEqual(len(builder.closed), 0)

        builder.reset_after_reconnect()
        self.assertTrue(builder.status()["reconnect_warmup_pending"])

        # Resume in the same minute, then let the incomplete bucket close.
        builder.add_trade(self.trade(100, second=20))
        builder.add_trade(self.trade(101))
        status = builder.status()
        self.assertFalse(status["reconnect_warmup_pending"])
        self.assertFalse(builder.is_ready)
        self.assertEqual(status["partial_buckets_discarded"], 1)

        # A complete subsequent candle can now be accumulated normally.
        builder.add_trade(self.trade(102))
        self.assertEqual(len(builder.closed), 1)
        self.assertFalse(builder.status()["reconnect_warmup_pending"])

    def test_reconnect_reset_counters_explain_warmup_restarts(self):
        builder = TradeCandleBuilder("BTC_USDT")
        builder.add_trade(self.trade(100))
        builder.add_trade(self.trade(101))
        self.assertEqual(builder.status()["partial_buckets_discarded"], 1)

        builder.add_trade(self.trade(102))
        builder.reset_after_reconnect()
        self.assertEqual(builder.status()["reconnect_count"], 1)

        builder.add_trade(self.trade(102, second=20))
        builder.add_trade(self.trade(103))
        status = builder.status()
        self.assertEqual(status["partial_buckets_discarded"], 2)
        self.assertEqual(status["history_clear_count"], 0)
        self.assertEqual(status["last_reset_reason"], "reconnect_partial_bucket_discarded_history_retained")

    def test_reconnect_rejects_replayed_older_tick_before_resuming_same_minute(self):
        builder = TradeCandleBuilder("BTC_USDT")
        for minute in range(100, 125):
            builder.add_trade(self.trade(minute))
        self.assertTrue(builder.is_ready)
        last_timestamp = builder.last_timestamp_ms
        forming_minute = builder.current["open_time"] // 60_000

        builder.reset_after_reconnect()
        replay = builder.add_trade(self.trade(forming_minute, second=0))
        self.assertFalse(replay["accepted"])
        self.assertEqual(replay["reason"], "out_of_order")
        self.assertEqual(builder._resume_bucket, forming_minute * 60_000)
        self.assertEqual(builder.last_timestamp_ms, None)

        fresh = builder.add_trade(self.trade(forming_minute, second=2))
        self.assertTrue(fresh["accepted"])
        self.assertFalse(fresh["gap_detected"])
        self.assertGreater(builder.last_timestamp_ms, last_timestamp)
        self.assertFalse(builder.is_ready)

    def test_reconnect_rejects_equal_timestamp_replay_without_sequence_id(self):
        builder = TradeCandleBuilder("BTC_USDT")
        builder.add_trade(self.trade(100, second=10, sequence=None))
        previous_timestamp = builder.last_timestamp_ms

        builder.reset_after_reconnect()
        replay = {
            "symbol": "BTC_USDT", "price": 101, "quantity": 3,
            "timestamp_ms": previous_timestamp,
        }
        result = builder.add_trade(replay)
        self.assertFalse(result["accepted"])
        self.assertEqual(result["reason"], "out_of_order")
        self.assertEqual(builder._resume_bucket, 100 * 60_000)
        self.assertIsNone(builder.current)

        fresh = {
            "symbol": "BTC_USDT", "price": 102, "quantity": 1,
            "timestamp_ms": previous_timestamp + 1,
        }
        accepted = builder.add_trade(fresh)
        self.assertTrue(accepted["accepted"])
        self.assertFalse(accepted["gap_detected"])
        self.assertEqual(builder.current["volume"], 1.0)

    def test_reconnect_at_adjacent_minute_boundary_restarts_contiguous_warmup(self):
        builder = TradeCandleBuilder("BTC_USDT")
        for minute in range(100, 125):
            builder.add_trade(self.trade(minute))
        self.assertTrue(builder.is_ready)
        builder.reset_after_reconnect()

        result = builder.add_trade(self.trade(124, second=20))
        self.assertTrue(result["accepted"])
        self.assertFalse(result["gap_detected"])
        self.assertFalse(builder.is_ready)

        # Discard the partial reconnect bucket, then wait for one fully observed
        # post-reconnect candle before analysis becomes ready.
        builder.add_trade(self.trade(125))
        self.assertEqual(len(builder.closed), 23)
        self.assertFalse(builder.is_ready)
        builder.add_trade(self.trade(126))
        self.assertEqual(len(builder.closed), 24)
        self.assertTrue(builder.is_ready)

    def test_reconnect_with_one_missing_minute_retains_history_after_full_bar(self):
        builder = TradeCandleBuilder("BTC_USDT")
        for minute in range(100, 125):
            builder.add_trade(self.trade(minute))
        self.assertTrue(builder.is_ready)
        builder.reset_after_reconnect()

        result = builder.add_trade(self.trade(126))
        self.assertTrue(result["accepted"])
        self.assertTrue(result["gap_detected"])
        self.assertEqual(len(builder.closed), 23)
        self.assertFalse(builder.is_ready)
        # 126 was only partially observed; 127 is the first full new minute.
        builder.add_trade(self.trade(127))
        self.assertEqual(len(builder.closed), 23)
        self.assertFalse(builder.is_ready)
        builder.add_trade(self.trade(128))
        self.assertEqual(len(builder.closed), 24)
        self.assertTrue(builder.is_ready)
        self.assertEqual(builder.status()["history_clear_count"], 0)

    def test_long_reconnect_gap_still_clears_history(self):
        builder = TradeCandleBuilder("BTC_USDT")
        for minute in range(100, 125):
            builder.add_trade(self.trade(minute))
        builder.reset_after_reconnect()
        result = builder.add_trade(self.trade(128))
        self.assertTrue(result["gap_detected"])
        self.assertEqual(len(builder.closed), 0)
        self.assertFalse(builder.is_ready)
        self.assertEqual(builder.status()["history_clear_count"], 1)

    def test_hard_gap_reset_discards_all_warmup_data(self):
        builder = TradeCandleBuilder("BTC_USDT")
        builder.add_trade(self.trade(100))
        builder.add_trade(self.trade(101))
        self.assertEqual(len(builder.closed), 0)
        builder.reset_after_gap()
        self.assertEqual(len(builder.closed), 0)
        self.assertIsNone(builder.current)
        self.assertFalse(builder.is_ready)

    def test_restored_cache_continues_when_first_live_minute_is_adjacent(self):
        builder = TradeCandleBuilder("BTC_USDT")
        rows = [[minute * 60_000, 100, 102, 99, 101, 4]
                for minute in range(100, 123)]
        self.assertEqual(builder.restore_closed_candles(rows), 23)
        self.assertFalse(builder.is_ready)

        # The cached 23 closed rows are safe while the first live bucket is
        # forming because analysis excludes that incomplete current bucket.
        result = builder.add_trade(self.trade(123, second=2))
        self.assertTrue(result["accepted"])
        self.assertFalse(result["gap_detected"])
        self.assertTrue(builder.is_ready)
        self.assertEqual(len(builder.closed), 23)
        # The forming row is excluded; technical indicators see only the
        # validated, fully closed cached candles.
        technical = analyze_klines(builder.candles_for_analysis())
        self.assertEqual(technical["closed_candles"], 23)

        # At rollover the partial bucket is dropped, then the next full candle
        # is retained. The restored closed cache is not needlessly discarded.
        rollover = builder.add_trade(self.trade(124, second=2))
        self.assertFalse(rollover["gap_detected"])
        self.assertEqual(len(builder.closed), 23)
        self.assertFalse(builder.is_ready)
        builder.add_trade(self.trade(125, second=2))
        self.assertEqual(len(builder.closed), 24)
        self.assertTrue(builder.is_ready)
        self.assertEqual(builder.status()["history_clear_count"], 0)

    def test_restores_a_single_closed_candle_and_discards_first_partial_bucket(self):
        builder = TradeCandleBuilder("BTC_USDT")
        row = [[100 * 60_000, 100, 102, 99, 101, 4]]
        self.assertEqual(builder.restore_closed_candles(row), 1)
        first = builder.add_trade(self.trade(101, second=2))
        self.assertTrue(first["accepted"])
        self.assertFalse(first["gap_detected"])
        self.assertEqual(len(builder.closed), 1)
        rollover = builder.add_trade(self.trade(102, second=2))
        self.assertFalse(rollover["gap_detected"])
        self.assertEqual(len(builder.closed), 1)
        self.assertEqual(builder.status()["history_clear_count"], 0)
        builder.add_trade(self.trade(103, second=2))
        self.assertEqual(len(builder.closed), 2)
        self.assertEqual(builder.status()["history_clear_count"], 0)
        # Minute 102 has been observed continuously from its start boundary;
        # it can close as the first complete post-startup candle.
        builder.add_trade(self.trade(103, second=2))
        self.assertEqual(len(builder.closed), 1)
        self.assertEqual(builder.closed[0][0], 102 * 60_000)

    def test_restored_cache_survives_one_missing_minute_but_waits_for_full_bar(self):
        builder = TradeCandleBuilder("BTC_USDT")
        rows = [[minute * 60_000, 100, 102, 99, 101, 4]
                for minute in range(100, 123)]
        builder.restore_closed_candles(rows)
        result = builder.add_trade(self.trade(124, second=2))
        self.assertTrue(result["accepted"])
        self.assertTrue(result["gap_detected"])
        self.assertEqual(len(builder.closed), 23)
        self.assertFalse(builder.is_ready)
        builder.add_trade(self.trade(125))
        self.assertEqual(len(builder.closed), 23)
        self.assertFalse(builder.is_ready)
        builder.add_trade(self.trade(126))
        self.assertEqual(len(builder.closed), 24)
        self.assertTrue(builder.is_ready)

    def test_restore_rejects_short_or_discontinuous_cache(self):
        builder = TradeCandleBuilder("BTC_USDT")
        with self.assertRaisesRegex(ValueError, "1"):
            builder.restore_closed_candles([])
        rows = [[minute * 60_000, 100, 102, 99, 101, 4]
                for minute in range(100, 121)]
        rows[10][0] += 60_000
        with self.assertRaisesRegex(ValueError, "gap"):
            builder.restore_closed_candles(rows)

    def test_restores_five_candle_cache_for_monitoring_but_not_analysis(self):
        builder = TradeCandleBuilder("BTC_USDT")
        rows = [[minute * 60_000, 100, 102, 99, 101, 4]
                for minute in range(100, 105)]
        self.assertEqual(builder.restore_closed_candles(rows), 5)
        self.assertTrue(builder.monitor_ready)
        self.assertFalse(builder.is_ready)
        trend = builder.preliminary_trend()
        self.assertIsNotNone(trend)
        self.assertFalse(trend["trade_allowed"])
        self.assertIsNone(trend["signal"])

    def test_watermark_finalizes_only_complete_bucket_after_lateness_grace(self):
        builder = TradeCandleBuilder("BTC_USDT")
        builder.add_trade(self.trade(100, second=1))
        builder.add_trade(self.trade(101, second=1))
        due_at = 102 * 60_000 + 30_000

        early = builder.finalize_due(due_at - 1)
        self.assertFalse(early["finalized"])
        self.assertEqual(early["reason"], "watermark_not_due")
        closed = builder.finalize_due(due_at)
        self.assertTrue(closed["finalized"])
        self.assertEqual(closed["closed_open_time"], 101 * 60_000)
        self.assertIsNone(builder.current)
        self.assertEqual(builder.closed[-1][0], 101 * 60_000)

    def test_watermark_never_finalizes_initial_or_reconnect_partial_bucket(self):
        builder = TradeCandleBuilder("BTC_USDT")
        builder.add_trade(self.trade(100, second=1))
        partial = builder.finalize_due(110 * 60_000)
        self.assertFalse(partial["finalized"])
        self.assertEqual(partial["reason"], "partial_or_reconnect_bucket")

        builder.add_trade(self.trade(101, second=1))
        builder.reset_after_reconnect()
        builder.add_trade(self.trade(101, second=20))
        reconnect_partial = builder.finalize_due(110 * 60_000)
        self.assertFalse(reconnect_partial["finalized"])
        self.assertEqual(reconnect_partial["reason"], "partial_or_reconnect_bucket")

    def test_late_trade_for_watermark_finalized_bucket_is_rejected(self):
        builder = TradeCandleBuilder("BTC_USDT")
        builder.add_trade(self.trade(100, second=1))
        builder.add_trade(self.trade(101, second=1))
        builder.finalize_due(102 * 60_000 + 30_000)

        late = builder.add_trade(self.trade(101, second=50))
        self.assertFalse(late["accepted"])
        self.assertEqual(late["reason"], "late_after_finalization")
        self.assertEqual(builder.closed[-1][0], 101 * 60_000)

    def test_gap_after_watermark_finalization_clears_history(self):
        builder = TradeCandleBuilder("BTC_USDT")
        builder.add_trade(self.trade(100, second=1))
        builder.add_trade(self.trade(101, second=1))
        builder.finalize_due(102 * 60_000 + 30_000)

        gap = builder.add_trade(self.trade(103, second=1))
        self.assertTrue(gap["accepted"])
        self.assertTrue(gap["gap_detected"])
        self.assertEqual(len(builder.closed), 0)
        self.assertEqual(builder.status()["last_reset_reason"],
                         "timestamp_gap_after_finalization")

    def test_contiguous_trade_after_watermark_finalization_starts_new_forming_bucket(self):
        builder = TradeCandleBuilder("BTC_USDT")
        builder.add_trade(self.trade(100, second=1))
        builder.add_trade(self.trade(101, second=1))
        builder.finalize_due(102 * 60_000 + 30_000)

        next_trade = builder.add_trade(self.trade(102, second=2))
        self.assertTrue(next_trade["accepted"])
        self.assertFalse(next_trade["gap_detected"])
        self.assertEqual(len(builder.closed), 1)
        self.assertEqual(builder.current["open_time"], 102 * 60_000)

    def test_invalid_builder_configuration(self):
        for symbol in ("BTCUSDT", "BTC/USDT", "BTC__USDT", ""):
            with self.subTest(symbol=symbol):
                with self.assertRaises(ValueError):
                    TradeCandleBuilder(symbol)
        with self.assertRaises(ValueError):
            TradeCandleBuilder("BTC_USDT", interval_ms=0)
        with self.assertRaises(ValueError):
            TradeCandleBuilder("BTC_USDT", max_candles=10)

    def test_noncontiguous_sequence_ids_are_not_assumed_to_mean_lost_trades(self):
        builder = TradeCandleBuilder("BTC_USDT")
        first = builder.add_trade(self.trade(100, second=10, sequence=100))
        later = builder.add_trade(self.trade(100, second=20, sequence=107))
        self.assertTrue(first["accepted"])
        self.assertTrue(later["accepted"])
        self.assertFalse(later["gap_detected"])
        self.assertEqual(builder.current["volume"], 4.0)
        self.assertEqual(builder.last_sequence, 107)

    def test_older_sequence_is_distinguished_from_exact_duplicate(self):
        builder = TradeCandleBuilder("BTC_USDT")
        self.assertTrue(builder.add_trade(self.trade(100, second=10, sequence=12))["accepted"])
        duplicate = builder.add_trade(self.trade(100, second=20, sequence=12))
        older = builder.add_trade(self.trade(100, second=30, sequence=11))
        self.assertEqual(duplicate["reason"], "duplicate_sequence")
        self.assertEqual(older["reason"], "older_sequence")
        self.assertEqual(builder.status()["rejection_reasons"], {
            "duplicate_sequence": 1, "older_sequence": 1
        })

    def test_rejection_reasons_are_counted_without_losing_total(self):
        builder = TradeCandleBuilder("BTC_USDT")
        builder.add_trade(self.trade(100, second=20, sequence=9))
        builder.add_trade(self.trade(100, second=10, sequence=10))
        builder.add_trade(self.trade(100, second=30, sequence=9))
        status = builder.status()
        self.assertEqual(status["rejected_trades"], 2)
        self.assertEqual(status["rejection_reasons"], {
            "out_of_order": 1, "duplicate_sequence": 1
        })


    def test_volume_overflow_resets_all_candle_history_and_fails_closed(self):
        builder = TradeCandleBuilder("BTC_USDT")
        builder.add_trade(self.trade(100, quantity=1e308))
        self.assertIsNotNone(builder.current)
        self.assertEqual(builder.current["volume"], 1e308)

        result = builder.add_trade(self.trade(100, second=2, quantity=1e308))
        self.assertFalse(result["accepted"])
        self.assertEqual(result["reason"], "volume_overflow")
        self.assertIsNone(builder.current)
        self.assertEqual(len(builder.closed), 0)
        self.assertFalse(builder.is_ready)
        status = builder.status()
        self.assertEqual(status["gaps"], 1)
        self.assertEqual(status["history_clear_count"], 1)
        self.assertEqual(status["last_reset_reason"], "explicit_gap_reset")


if __name__ == "__main__":
    unittest.main()
