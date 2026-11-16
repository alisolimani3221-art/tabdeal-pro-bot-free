import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class TabdealApiContractTests(unittest.TestCase):
    def test_android_app_does_not_call_unsupported_candle_or_ticker_paths(self):
        source = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertNotIn("/klines", source)
        self.assertNotIn("/ticker/24hr", source)
        self.assertNotIn("fapi.binance.com", source)

    def test_multimarket_monitor_uses_tabdeal_futures_api(self):
        source = (ROOT / "multi_bot.py").read_text(encoding="utf-8")
        self.assertIn("https://api1.tabdeal.org/r/fapi/v1", source)
        self.assertNotIn("fapi.binance.com", source)
        self.assertIn("/depth", source)

    def test_server_monitor_does_not_create_paper_trading_signals(self):
        source = (ROOT / "server_paper_trader.py").read_text(encoding="utf-8")
        self.assertIn("/depth", source)
        self.assertNotIn("/klines", source)
        self.assertNotIn("PaperTradingEngine", source)
        self.assertNotIn("new_order", source)


if __name__ == "__main__":
    unittest.main()
