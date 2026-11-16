import unittest
from paper_trader import PaperTradingEngine, rank_signal


def setup(symbol="AAA_USDT", price=100.0, signal="BULLISH"):
    if signal == "BULLISH":
        return {"symbol": symbol, "price": price, "sma20": 99, "sma50": 98,
                "rsi14": 56, "atr14": 0.8, "signal": signal}
    return {"symbol": symbol, "price": price, "sma20": 101, "sma50": 102,
            "rsi14": 44, "atr14": 0.8, "signal": signal}


class PaperTraderTests(unittest.TestCase):
    def test_rejects_neutral_or_extreme_volatility(self):
        self.assertIsNone(rank_signal(dict(setup(), signal="NEUTRAL")))
        self.assertIsNone(rank_signal(dict(setup(), atr14=3.0)))

    def test_opens_only_paper_position_with_eight_dollar_capital(self):
        engine = PaperTradingEngine(capital=8)
        result = engine.process_scan([setup()])
        self.assertEqual(result["events"][0]["event"], "OPENED")
        self.assertEqual(result["open_position"]["symbol"], "AAA_USDT")
        self.assertLessEqual(result["open_position"]["notional"] /
                             result["open_position"]["leverage"], 2.0)
        self.assertLessEqual(result["open_position"]["risk_budget"], 0.04)

    def test_stop_closes_position_and_records_net_result(self):
        engine = PaperTradingEngine(capital=8, fee_rate=0.0005, slippage_rate=0)
        engine.process_scan([setup()])
        stopped = dict(setup(price=98.0))
        result = engine.process_scan([stopped])
        self.assertTrue(any(e["event"] == "CLOSED" for e in result["events"]))
        self.assertIsNone(result["open_position"])
        self.assertEqual(engine.closed_trades[-1]["reason"], "STOP")

    def test_one_position_at_a_time(self):
        engine = PaperTradingEngine(capital=8)
        engine.process_scan([setup()])
        result = engine.process_scan([setup("BBB_USDT"), setup("CCC_USDT")])
        self.assertEqual(result["open_position"]["symbol"], "AAA_USDT")

    def test_risk_and_leverage_caps_are_enforced(self):
        with self.assertRaises(ValueError):
            PaperTradingEngine(capital=8, risk_percent=1)
        with self.assertRaises(ValueError):
            PaperTradingEngine(capital=8, max_leverage=5)


if __name__ == "__main__":
    unittest.main()
