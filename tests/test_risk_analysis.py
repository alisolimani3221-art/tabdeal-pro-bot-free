import unittest

from risk_analysis import analyze_klines, calculate_position_risk, recommend_leverage


def candles(count=56):
    rows = []
    for i in range(count):
        close = 100 + i
        rows.append([i * 60_000, close - 1, close + 1, close - 2, close, 1])
    return rows


class RiskAnalysisTests(unittest.TestCase):
    def test_position_size_respects_risk_budget(self):
        result = calculate_position_risk(1000, 1, 50000, 2, 3, 2)
        self.assertAlmostEqual(result["risk_amount"], 10)
        self.assertAlmostEqual(result["quantity"] * 1000, result["risk_amount"])
        self.assertAlmostEqual(result["notional"], 500)
        self.assertAlmostEqual(result["margin_estimate"], 500 / 3)
        self.assertAlmostEqual(result["stop_price_long"], 49000)
        self.assertAlmostEqual(result["target_price_long"], 52000)

    def test_position_size_never_exceeds_collateral_leverage(self):
        result = calculate_position_risk(100, 1, 100, 0.01, 2, 2)
        self.assertLessEqual(result["margin_estimate"], 100)
        self.assertLessEqual(result["notional"], 200)
        self.assertLessEqual(result["risk_amount"], result["risk_limit"])

    def test_risk_limits_reject_excessive_risk(self):
        with self.assertRaises(ValueError):
            calculate_position_risk(1000, 6, 50000, 1, 3)

    def test_persian_digits_are_accepted(self):
        result = calculate_position_risk("۱۰۰۰", "۱", "۵۰۰۰۰", "۲", "۳")
        self.assertAlmostEqual(result["risk_amount"], 10)

    def test_rejects_boolean_nonfinite_and_malformed_risk_inputs(self):
        for args in (
            (True, 1, 100, 1, 2, 2),
            (100, False, 100, 1, 2, 2),
            (100, 1, float("nan"), 1, 2, 2),
            (100, 1, float("inf"), 1, 2, 2),
            (100, 1, 100, 0, 2, 2),
            (100, 1, 100, 1, 2.5, 2),
            (100, 1, 100, 1, 2, 0),
            (100, 1, 100, 1, 4, 2),
            (100, 1, 100, 1, 2, 11),
        ):
            with self.subTest(args=args):
                with self.assertRaises(ValueError):
                    calculate_position_risk(*args)

    def test_risk_caps_position_notional_and_collateral(self):
        result = calculate_position_risk(100, 5, 10, 0.01, 3, 2)
        self.assertLessEqual(result["notional"], 300)
        self.assertLessEqual(result["margin_estimate"], 100)
        self.assertLessEqual(result["risk_amount"], result["risk_limit"])

    def test_rejects_unrepresentable_calculation(self):
        with self.assertRaises(ValueError):
            calculate_position_risk(1e308, 5, 1e-308, 1, 3, 2)

    def test_analysis_accepts_20_closed_candles_and_rejects_19(self):
        rows = candles(21)
        result = analyze_klines(rows)
        self.assertEqual(result["closed_candles"], 20)
        self.assertIn("sma10", result)
        self.assertIn("sma20", result)
        with self.assertRaisesRegex(ValueError, "at least 21 candles"):
            analyze_klines(candles(20))

    def test_analysis_ignores_open_candle(self):
        rows = candles(56)
        rows[-1] = [56, 1000, 2000, 1, 1500, 1]
        result = analyze_klines(rows)
        self.assertAlmostEqual(result["price"], 154)
        self.assertGreater(result["sma10"], result["sma20"])
        self.assertEqual(result["closed_candles"], 55)

    def test_analysis_rejects_close_outside_candle_range(self):
        rows = candles()
        rows[-2] = [54, 100, 102, 99, 105, 1]
        with self.assertRaises(ValueError):
            analyze_klines(rows)

    def test_analysis_rejects_inconsistent_ohlc(self):
        rows = candles()
        rows[10] = [10, 105, 104, 103, 103.5, 1]
        with self.assertRaises(ValueError):
            analyze_klines(rows)

    def test_analysis_rejects_non_increasing_timestamps(self):
        rows = candles()
        rows[20][0] = rows[19][0]
        with self.assertRaises(ValueError):
            analyze_klines(rows)

    def test_analysis_rejects_gaps_in_one_minute_history(self):
        rows = candles()
        rows[20][0] += 60_000
        for index in range(21, len(rows)):
            rows[index][0] += 60_000
        with self.assertRaisesRegex(ValueError, "gap-free"):
            analyze_klines(rows)

    def test_analysis_rejects_negative_timestamp_or_volume(self):
        for index, value in ((0, -1), (5, -0.1)):
            rows = candles()
            rows[10][index] = value
            with self.subTest(index=index):
                with self.assertRaises(ValueError):
                    analyze_klines(rows)

    def test_analysis_rejects_boolean_nonfinite_and_nonpositive_ohlc(self):
        for index, value in ((1, True), (2, float("inf")), (3, 0),
                             (4, float("nan")), (5, float("nan"))):
            rows = candles()
            rows[10][index] = value
            with self.subTest(index=index, value=value):
                with self.assertRaises(ValueError):
                    analyze_klines(rows)

    def test_analysis_rejects_malformed_rows_and_insufficient_data(self):
        with self.assertRaises(ValueError):
            analyze_klines([[0, 1, 2, 0.5, 1.5, 1]] * 10)
        for bad in (None, "bad", {}, [1, 2, 3]):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    analyze_klines(bad)

    def test_zero_atr_never_recommends_leverage(self):
        with self.assertRaises(ValueError):
            recommend_leverage({"signal": "BULLISH", "price": 100, "atr14": 0})

    def test_high_volatility_disables_trade(self):
        result = recommend_leverage(
            {"signal": "BULLISH", "price": 100, "atr14": 2.1}, 3
        )
        self.assertEqual(result["leverage"], 1)
        self.assertFalse(result["trade_allowed"])

    def test_liquidation_warning_matches_three_x_cap(self):
        result = calculate_position_risk(1000, 1, 50000, 2, 3, 2)
        self.assertTrue(result["liquidation_warning"])

    def test_recommend_leverage_rejects_invalid_inputs(self):
        for technical, cap in (
            (None, 3),
            ({"signal": "BULLISH", "price": True, "atr14": 1}, 3),
            ({"signal": "BULLISH", "price": 100, "atr14": float("nan")}, 3),
            ({"signal": "BULLISH", "price": 1e-308, "atr14": 1e308}, 3),
            ({"signal": "BULLISH", "price": 100, "atr14": 1}, 3.5),
            ({"signal": "BULLISH", "price": 100, "atr14": 1}, True),
        ):
            with self.subTest(technical=technical, cap=cap):
                with self.assertRaises(ValueError):
                    recommend_leverage(technical, cap)

    def test_neutral_signal_never_allows_trade(self):
        result = recommend_leverage(
            {"signal": "NEUTRAL", "price": 100, "atr14": 0.5}, 3
        )
        self.assertFalse(result["trade_allowed"])
        self.assertEqual(result["leverage"], 1)


if __name__ == "__main__":
    unittest.main()
