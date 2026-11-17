import unittest

from risk_analysis import analyze_klines, calculate_position_risk, recommend_leverage


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

    def test_analysis_ignores_open_candle(self):
        candles = []
        for i in range(55):
            close = 100 + i
            candles.append([i, close - 1, close + 1, close - 2, close, 1])
        # Deliberately extreme unfinished candle; it must not affect results.
        candles.append([56, 1000, 2000, 1, 1500, 1])
        result = analyze_klines(candles)
        self.assertAlmostEqual(result["price"], 154)
        self.assertGreater(result["sma20"], result["sma50"])
        self.assertEqual(result["closed_candles"], 55)

    def test_analysis_rejects_close_outside_candle_range(self):
        candles = []
        for i in range(52):
            candles.append([i, 100, 102, 99, 101, 1])
        candles[-2] = [50, 100, 102, 99, 105, 1]
        with self.assertRaises(ValueError):
            analyze_klines(candles)

    def test_risk_rejects_nonpositive_short_target(self):
        with self.assertRaises(ValueError):
            calculate_position_risk(1000, 1, 100, 20, 2, 5)

    def test_zero_atr_never_recommends_leverage(self):

        from risk_analysis import recommend_leverage
        with self.assertRaises(ValueError):
            recommend_leverage({
                "signal": "BULLISH", "price": 100, "atr14": 0
            })

    def test_analysis_rejects_insufficient_data(self):
        with self.assertRaises(ValueError):
            analyze_klines([[0, 1, 2, 0.5, 1.5, 1]] * 10)


    def test_analysis_rejects_inconsistent_ohlc(self):
        candles = []
        for i in range(55):
            close = 100 + i
            candles.append([i, close - 1, close + 1, close - 2, close, 1])
        candles.append([55, 1000, 2000, 1, 1500, 1])
        candles[10] = [10, 105, 104, 103, 103.5, 1]
        with self.assertRaises(ValueError):
            analyze_klines(candles)

    def test_analysis_rejects_non_increasing_timestamps(self):
        candles = []
        for i in range(55):
            close = 100 + i
            candles.append([i, close - 1, close + 1, close - 2, close, 1])
        candles.append([55, 1000, 2000, 1, 1500, 1])
        candles[20][0] = candles[19][0]
        with self.assertRaises(ValueError):
            analyze_klines(candles)

    def test_high_volatility_disables_trade(self):
        result = recommend_leverage(
            {"signal": "BULLISH", "price": 100, "atr14": 2.1}, 3
        )
        self.assertEqual(result["leverage"], 1)
        self.assertFalse(result["trade_allowed"])

    def test_liquidation_warning_matches_three_x_cap(self):
        result = calculate_position_risk(1000, 1, 50000, 2, 3, 2)
        self.assertTrue(result["liquidation_warning"])


if __name__ == "__main__":
    unittest.main()
