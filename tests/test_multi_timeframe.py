import unittest

from multi_timeframe import aggregate_closed_timeframes, analyze_multi_timeframe


def minute_rows(count=120):
    rows = []
    for index in range(count):
        timestamp = index * 60_000
        base = 100.0 + index * 0.01
        rows.append([
            timestamp, base, base + 0.2, base - 0.2,
            base + 0.05, 2.0,
        ])
    return rows


class ClosedTimeframeAggregationTests(unittest.TestCase):
    def test_emits_only_complete_epoch_aligned_bars(self):
        rows = minute_rows(120)
        result = aggregate_closed_timeframes(rows, now_ms=120 * 60_000)
        self.assertEqual([len(result[m]) for m in (5, 15, 30, 60)],
                         [24, 8, 4, 2])
        first = result[5][0]
        self.assertEqual(first[0], 0)
        self.assertEqual(first[1], rows[0][1])
        self.assertEqual(first[2], max(row[2] for row in rows[:5]))
        self.assertEqual(first[3], min(row[3] for row in rows[:5]))
        self.assertEqual(first[4], rows[4][4])
        self.assertEqual(first[5], 10.0)

    def test_does_not_emit_a_timeframe_bar_before_its_close(self):
        rows = minute_rows(60)
        # The 60th one-minute candle starts at 59m and closes at 60m.
        result = aggregate_closed_timeframes(rows, now_ms=59 * 60_000 + 30_000)
        self.assertEqual(len(result[60]), 0)
        self.assertEqual(len(result[30]), 1)
        self.assertEqual(len(result[15]), 3)

    def test_incomplete_leading_bucket_is_not_emitted(self):
        rows = minute_rows(60)[1:]
        result = aggregate_closed_timeframes(rows, now_ms=60 * 60_000)
        self.assertEqual(len(result[5]), 11)
        self.assertEqual(result[5][0][0], 5 * 60_000)

    def test_missing_or_duplicate_minute_fails_closed(self):
        rows = minute_rows(60)
        with self.assertRaisesRegex(ValueError, "gap or duplicate"):
            aggregate_closed_timeframes(rows[:10] + rows[11:], 60 * 60_000)
        with self.assertRaisesRegex(ValueError, "gap or duplicate"):
            aggregate_closed_timeframes(rows[:10] + [rows[9]] + rows[10:], 60 * 60_000)

    def test_invalid_ohlcv_and_server_clock_are_rejected(self):
        rows = minute_rows(60)
        broken = [list(row) for row in rows]
        broken[4][2] = float("nan")
        with self.assertRaisesRegex(ValueError, "finite"):
            aggregate_closed_timeframes(broken, 60 * 60_000)
        for clock in (True, 0, -1, "bad", 60000.5):
            with self.subTest(clock=clock), self.assertRaises(ValueError):
                aggregate_closed_timeframes(rows, clock)

    def test_non_aligned_minute_timestamp_is_rejected(self):
        rows = minute_rows(60)
        rows[3][0] += 1
        with self.assertRaisesRegex(ValueError, "aligned"):
            aggregate_closed_timeframes(rows, 60 * 60_000)


    def test_multi_timeframe_analyzer_requires_and_reports_full_agreement(self):
        rows = minute_rows(24 * 60)
        calls = []
        def stub_analyzer(candles, interval_ms=60_000):
            calls.append((len(candles) - 1, interval_ms))
            return {"signal": "BULLISH", "price": 100.0,
                    "closed_candles": len(candles) - 1,
                    "sma10": 99.0, "sma20": 98.0,
                    "rsi14": 56.0, "atr14": 0.5}
        result = analyze_multi_timeframe(
            rows, now_ms=24 * 60 * 60_000, analyzer=stub_analyzer
        )
        self.assertEqual(result["signal"], "BULLISH")
        self.assertTrue(result["timeframe_analysis"]["ready"])
        self.assertTrue(result["timeframe_analysis"]["agreement"])
        self.assertEqual(
            result["timeframe_analysis"]["signals"],
            {"5": "BULLISH", "15": "BULLISH",
             "30": "BULLISH", "60": "BULLISH"},
        )
        self.assertEqual(
            result["timeframe_analysis"]["closed_candles"],
            {"5": 288, "15": 96, "30": 48, "60": 24},
        )
        self.assertEqual({interval for _, interval in calls},
                         {5 * 60_000, 15 * 60_000, 30 * 60_000, 60 * 60_000})

    def test_multi_timeframe_disagreement_forces_neutral(self):
        rows = minute_rows(24 * 60)
        def stub_analyzer(candles, interval_ms=60_000):
            direction = "BEARISH" if interval_ms == 30 * 60_000 else "BULLISH"
            return {"signal": direction, "price": 100.0,
                    "closed_candles": len(candles) - 1}
        result = analyze_multi_timeframe(
            rows, now_ms=24 * 60 * 60_000, analyzer=stub_analyzer
        )
        self.assertEqual(result["signal"], "NEUTRAL")
        self.assertFalse(result["timeframe_analysis"]["agreement"])

    def test_multi_timeframe_insufficient_hourly_history_blocks_agreement(self):
        rows = minute_rows(22 * 60)
        def stub_analyzer(candles, interval_ms=60_000):
            return {"signal": "BULLISH", "price": 100.0,
                    "closed_candles": len(candles) - 1}
        result = analyze_multi_timeframe(
            rows, now_ms=22 * 60 * 60_000, analyzer=stub_analyzer
        )
        self.assertFalse(result["timeframe_analysis"]["ready"])
        self.assertFalse(result["timeframe_analysis"]["agreement"])
        self.assertEqual(result["signal"], "NEUTRAL")



if __name__ == "__main__":
    unittest.main()
