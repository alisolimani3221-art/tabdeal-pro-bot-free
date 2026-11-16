import unittest

from risk_analysis import analyze_klines, calculate_position_risk


class RiskAnalysisTests(unittest.TestCase):
    def test_position_size_respects_risk_budget(self):
        result = calculate_position_risk(1000, 1, 50000, 2, 3, 2)
        self.assertAlmostEqual(result["risk_amount"], 10)
        self.assertAlmostEqual(result["quantity"] * 1000, result["risk_amount"])
        self.assertAlmostEqual(result["notional"], 500)
        self.assertAlmostEqual(result["margin_estimate"], 500 / 3)
        self.assertAlmostEqual(result["stop_price_long"], 49000)
        self.assertAlmostEqual(result["target_price_long"], 52000)

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

    def test_analysis_rejects_insufficient_data(self):
        with self.assertRaises(ValueError):
            analyze_klines([[0, 1, 2, 0.5, 1.5, 1]] * 10)


if __name__ == "__main__":
    unittest.main()
