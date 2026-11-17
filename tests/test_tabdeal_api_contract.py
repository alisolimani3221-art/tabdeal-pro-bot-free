import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class TabdealApiContractTests(unittest.TestCase):
    def read(self, name):
        return (ROOT / name).read_text(encoding="utf-8")

    def test_android_app_does_not_call_unsupported_candle_or_ticker_paths(self):
        source = self.read("main.py")
        self.assertNotIn("/klines", source)
        self.assertNotIn("/ticker/24hr", source)
        self.assertNotIn("fapi.binance.com", source)

    def test_all_market_monitors_use_tabdeal_futures_api(self):
        for name in ("main.py", "multi_bot.py", "server_paper_trader.py"):
            with self.subTest(name=name):
                source = self.read(name)
                self.assertIn("api1.tabdeal.org/r/fapi/v1", source)
                self.assertNotIn("fapi.binance.com", source)

    def test_multimarket_monitor_uses_exchange_info_and_depth(self):
        source = self.read("multi_bot.py")
        self.assertIn("/exchangeInfo", source)
        self.assertIn("/depth", source)

    def test_android_app_does_not_enable_unverified_candle_signals(self):
        source = self.read("main.py")
        self.assertIn("technical_candles", source)
        self.assertIn("run_auto_paper_scanner", source)
        self.assertIn("هیچ سیگنال یا معامله آزمایشی ساخته نمی‌شود", source)

    def test_public_trade_probe_is_read_only_and_has_no_credentials(self):
        source = self.read("trade_stream_probe.py")
        self.assertIn("BROADCAST_URL", source)
        self.assertIn("ws.send(symbol)", source)
        self.assertNotIn("api_secret", source.lower())
        self.assertNotIn("api_key", source.lower())
        self.assertNotIn("new_order", source.lower())
        self.assertNotIn("create_order", source.lower())

    def test_server_monitor_does_not_create_paper_trading_signals_or_orders(self):
        source = self.read("server_paper_trader.py")
        self.assertIn("/depth", source)
        self.assertNotIn("/klines", source)
        self.assertNotIn("PaperTradingEngine", source)
        self.assertNotIn("new_order", source)
        self.assertNotIn("create_order", source)

    def test_docker_monitor_does_not_advertise_unused_paper_settings(self):
        compose = self.read("docker-compose.paper.yml")
        self.assertIn("tabdeal-market-monitor", compose)
        self.assertNotIn("PAPER_CAPITAL_USDT", compose)
        self.assertNotIn("PAPER_STATE_PATH", compose)

    def test_app_and_buildozer_versions_match(self):
        app = self.read("main.py")
        spec = self.read("buildozer.spec")
        app_version = re.search(r'^APP_VERSION\s*=\s*["\']([^"\']+)["\']', app, re.M)
        package_version = re.search(r'^version\s*=\s*(\S+)', spec, re.M)
        self.assertIsNotNone(app_version)
        self.assertIsNotNone(package_version)
        self.assertEqual(app_version.group(1), package_version.group(1))


if __name__ == "__main__":
    unittest.main()
