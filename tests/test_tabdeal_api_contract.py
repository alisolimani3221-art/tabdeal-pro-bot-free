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
        # Per-symbol builders are owned by the coordinator. Each accepted,
        # normalized broadcast event is routed once before tick-level risk handling.
        self.assertIn("MultiSymbolPaperCoordinator", source)
        self.assertIn("update = coordinator.on_trade(parsed, received_at=time.monotonic())", source)
        self.assertIn('if update.get("gap_detected"):', source)
        self.assertIn("# Every accepted, fresh trade may manage an open paper position", source)
        self.assertIn("coordinator.reconcile_tick(", source)
        self.assertIn("coordinator.candidate_snapshots(now=now)", source)
        self.assertNotIn("builder.reset_after_gap()", source)
        self.assertNotIn("builder.reset_after_gap()", source)
        self.assertIn("risk_percent=0.5", source)
        self.assertIn("max_leverage=3", source)
        self.assertIn("هیچ سفارش واقعی ارسال نمی‌شود", source)
        self.assertNotIn("/klines", source)
        self.assertNotIn("fapi.binance.com", source)

    def test_paper_worker_publishes_transport_and_selected_symbol_health(self):
        source = self.read("main.py")
        diagnostics = self.read("ai_diagnostics.py")
        self.assertIn("websocket_connected=True", source)
        self.assertIn("last_any_market_trade_at=time.monotonic()", source)
        self.assertIn("last_selected_symbol_trade_at=time.monotonic()", source)
        self.assertIn("builder_ready=bool(builder.is_ready)", source)
        self.assertIn("last_scan_at=last_scan", source)
        self.assertIn('"health": _engine_health(app, worker_alive)', diagnostics)
        self.assertIn("WAITING_FOR_SELECTED_SYMBOL_TRADE", diagnostics)
        self.assertIn("SELECTED_SYMBOL_STALE", diagnostics)

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

    def test_ci_fails_when_redacted_trade_stream_probe_is_silent(self):
        workflow = self.read(".github/workflows/android.yml")
        self.assertIn("Read-only Tabdeal trade broadcast probe", workflow)
        self.assertNotIn("continue-on-error: true", workflow)
        self.assertIn('if report["messages"] == 0:', workflow)
        self.assertIn('if report["valid_trades"] == 0:', workflow)
        self.assertIn("trade-stream-probe-report.json", workflow)
        self.assertIn("Upload redacted trade-stream probe report", workflow)

    def test_websocket_app_uses_supported_default_timeout_api(self):
        source = self.read("main.py")
        probe = self.read("trade_stream_probe.py")
        run_block = source[source.index("app.run_forever("):source.index("except Exception as stream_exc:")]
        self.assertNotRegex(run_block, r"(?<![A-Za-z0-9_])timeout\s*=")
        # The Android runner must not apply a process-wide default timeout:
        # it would also constrain reads after the WebSocket handshake.
        self.assertNotIn("websocket.setdefaulttimeout(", source)
        self.assertIn("Do not change socket.setdefaulttimeout globally here.", source)
        # The standalone probe may bound its short-lived handshake, but must
        # not pass the unsupported timeout keyword to run_forever().
        self.assertIn("websocket.setdefaulttimeout(min(12.0, timeout_value))", probe)
        self.assertNotIn("timeout=min(12.0", probe)

    def test_public_trade_probe_uses_observed_plain_text_symbol_subscription(self):
        source = self.read("trade_stream_probe.py")
        self.assertIn("BROADCAST_URL", source)
        self.assertIn("ws.send(symbol)", source)
        self.assertIn("plain-text market symbol", source)
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
