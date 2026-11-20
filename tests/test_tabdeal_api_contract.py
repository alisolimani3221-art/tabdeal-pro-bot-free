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

    def test_android_paper_simulator_uses_verified_trade_candles_only(self):
        source = self.read("main.py")
        self.assertIn("TradeCandleBuilder", source)
        self.assertIn("PaperTradingEngine", source)
        self.assertIn("parse_trade_message", source)
        # Gap detection and history invalidation are owned by TradeCandleBuilder.add_trade().
        # The UI worker must consume that diagnostic rather than resetting twice.
        self.assertIn("update = builder.add_trade(parsed)", source)
        self.assertIn('if update.get("gap_detected"):', source)
        gap_guard = source.index('if update.get("gap_detected"):')
        tick_processing = source.index("engine.process_tick(symbol, parsed[\"price\"])")
        self.assertLess(gap_guard, tick_processing)
        self.assertNotIn("builder.reset_after_gap()", source)
        self.assertIn("risk_percent=0.5", source)
        self.assertIn("max_leverage=3", source)
        self.assertIn("هیچ سفارش واقعی ارسال نمی‌شود", source)
        self.assertNotIn("/klines", source)
        self.assertNotIn("fapi.binance.com", source)

    def test_observed_trade_schema_and_candle_safety_are_documented(self):
        doc = self.read("TRADE_STREAM_SCHEMA.md")
        source = self.read("main.py")
        candle_code = self.read("market_candles.py")
        self.assertIn("side_name", doc)
        self.assertIn("updated", doc)
        self.assertIn("one-minute", doc.lower())
        self.assertIn("data gap", doc.lower())
        self.assertIn("discard_next_close", candle_code)
        self.assertIn("TradeCandleBuilder", source)
        self.assertIn("هیچ سفارش واقعی ارسال نمی‌شود", source)

    def test_ci_runs_optional_redacted_trade_stream_probe(self):
        workflow = self.read(".github/workflows/android.yml")
        self.assertIn("Read-only Tabdeal trade broadcast probe", workflow)
        self.assertIn("continue-on-error: true", workflow)
        self.assertIn("trade-stream-probe-report.json", workflow)
        self.assertIn("Upload redacted trade-stream probe report", workflow)

    def test_public_trade_probe_is_read_only_and_has_no_credentials(self):
        source = self.read("trade_stream_probe.py")
        self.assertIn("BROADCAST_URL", source)
        self.assertIn("ws.send(symbol)", source)
        self.assertNotIn("api_secret", source.lower())
        self.assertNotIn("api_key", source.lower())
        self.assertNotIn("new_order", source.lower())
        self.assertNotIn("create_order", source.lower())

    def test_all_runtime_components_are_read_only_and_have_no_credentials(self):
        # The Android UI may collect credentials for its separate, explicitly
        # requested read-only account gateway. Public market monitors and paper
        # trading engines must remain credential-free.
        credential_free = ("multi_bot.py", "server_paper_trader.py",
                           "trade_stream_probe.py", "paper_trader.py")
        for name in credential_free:
            source = self.read(name).lower()
            with self.subTest(name=name):
                self.assertNotIn("api_key", source)
                self.assertNotIn("api_secret", source)

        # No runtime component may expose order-creation code or HTTP writes.
        for name in ("main.py", "multi_bot.py", "server_paper_trader.py",
                     "trade_stream_probe.py", "paper_trader.py"):
            source = self.read(name).lower()
            with self.subTest(name=name):
                self.assertNotIn("new_order", source)
                self.assertNotIn("create_order", source)
                self.assertNotRegex(source, r"\b(?:requests|session|self\.session)\.(?:post|put|delete)\s*\(")

    def test_private_account_client_uses_signed_read_gateway_and_is_get_only(self):
        source = self.read("tabdeal_account.py").lower()
        self.assertIn('read_base_url = "https://api1.tabdeal.org/r/fapi"', source)
        self.assertIn('"/v3/account"', source)
        self.assertIn('"/v3/positionrisk"', source)
        self.assertIn('"x-mbx-apikey"', source)
        self.assertNotRegex(source, r"\.(post|put|delete)\s*\(")
        self.assertNotIn("open(", source)
        self.assertNotIn("settings.json", source)
        self.assertNotIn("new_order", source)
        self.assertNotIn("create_order", source)

    def test_server_monitor_does_not_create_paper_trading_signals_or_orders(self):
        source = self.read("server_paper_trader.py")
        self.assertIn("/depth", source)
        self.assertNotIn("/klines", source)
        self.assertNotIn("PaperTradingEngine", source)
        self.assertNotIn("new_order", source)
        self.assertNotIn("create_order", source)

    def test_docker_monitor_is_hardened_and_runs_non_root(self):
        compose = self.read("docker-compose.paper.yml")
        dockerfile = self.read("Dockerfile.paper")
        self.assertIn("read_only: true", compose)
        self.assertIn("cap_drop:", compose)
        self.assertIn("no-new-privileges:true", compose)
        self.assertIn("mem_limit:", compose)
        self.assertIn("USER trader", dockerfile)

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
