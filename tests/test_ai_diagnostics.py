import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from ai_diagnostics import APP_VERSION, build_ai_diagnostic_context


class AIDiagnosticContextTests(unittest.TestCase):
    def test_context_reports_current_app_version(self):
        context = build_ai_diagnostic_context(SimpleNamespace())
        self.assertEqual(APP_VERSION, "1.54")
        self.assertEqual(context["app_version"], APP_VERSION)

    def test_context_is_allow_listed_and_excludes_account_data(self):
        app = SimpleNamespace(
            current_symbol="BTC_USDT",
            last_diagnostic_results={
                "market_ping": "OK",
                "depth_http": 200,
                "api_secret": "MUST_NOT_LEAK",
                "balance": "MUST_NOT_LEAK",
                "orders": ["MUST_NOT_LEAK"],
            },
            last_market_snapshot={
                "symbol": "BTC_USDT",
                "best_bid": 100,
                "best_ask": 101,
                "api_key": "MUST_NOT_LEAK",
                "account": {"balance": 99},
            },
            latest_technical_analysis={"rsi14": 52, "signal": "neutral",
                                       "private_token": "MUST_NOT_LEAK"},
            account_summary=SimpleNamespace(text="MUST_NOT_LEAK"),
            last_diagnostic_time="2026-11-21 19:00",
            auto_worker_status="idle",
            auto_worker_status_time="19:00",
        )
        context = build_ai_diagnostic_context(app)
        rendered = repr(context)
        self.assertEqual(context["diagnostics"]["market_ping"], "OK")
        self.assertEqual(context["market_snapshot"]["best_bid"], 100)
        self.assertEqual(context["technical_analysis"]["rsi14"], 52)
        self.assertEqual(context["access_mode"], "read_only")
        self.assertNotIn("MUST_NOT_LEAK", rendered)
        self.assertNotIn("api_secret", rendered)
        self.assertNotIn("account_summary", rendered)

    def test_context_bounds_lists_and_strings(self):
        app = SimpleNamespace(
            current_symbol="X" * 100,
            last_diagnostic_results={"market_error": "E" * 900},
            last_market_snapshot={"bids": [[100, 2]] * 20},
        )
        context = build_ai_diagnostic_context(app)
        self.assertEqual(len(context["selected_symbol"]), 40)
        self.assertEqual(len(context["diagnostics"]["market_error"]), 500)
        self.assertEqual(len(context["market_snapshot"]["bids"]), 10)
        self.assertEqual(context["market_snapshot"]["bids"][0], [100, 2])


    def test_thread_alive_without_socket_is_not_healthy(self):
        worker = Mock()
        worker.is_alive.return_value = True
        app = SimpleNamespace(auto_trader_thread=worker, auto_health={})
        health = build_ai_diagnostic_context(app)["engine"]["health"]
        self.assertTrue(health["thread_alive"])
        self.assertEqual(health["health_state"], "CONNECTING_OR_DISCONNECTED")

    def test_connected_but_no_selected_symbol_trade_is_waiting(self):
        worker = Mock()
        worker.is_alive.return_value = True
        app = SimpleNamespace(
            auto_trader_thread=worker,
            auto_health={"websocket_connected": True, "worker_phase": "connected_waiting_for_trade",
                         "valid_market_trades": 8},
        )
        health = build_ai_diagnostic_context(app)["engine"]["health"]
        self.assertEqual(health["health_state"], "WAITING_FOR_SELECTED_SYMBOL_TRADE")

    def test_selected_symbol_stale_data_is_not_active(self):
        import time
        worker = Mock()
        worker.is_alive.return_value = True
        app = SimpleNamespace(
            auto_trader_thread=worker,
            auto_health={"websocket_connected": True,
                         "last_selected_symbol_trade_at": time.monotonic() - 40,
                         "builder_ready": True, "last_scan_at": time.monotonic()},
        )
        health = build_ai_diagnostic_context(app)["engine"]["health"]
        self.assertEqual(health["health_state"], "SELECTED_SYMBOL_STALE")

    def test_builder_warmup_and_first_scan_are_distinguished(self):
        import time
        worker = Mock()
        worker.is_alive.return_value = True
        now = time.monotonic()
        base = {"websocket_connected": True, "last_selected_symbol_trade_at": now}
        warming = SimpleNamespace(auto_trader_thread=worker,
                                  auto_health=dict(base, builder_ready=False))
        self.assertEqual(
            build_ai_diagnostic_context(warming)["engine"]["health"]["health_state"],
            "WARMING_UP",
        )
        ready = SimpleNamespace(auto_trader_thread=worker,
                                auto_health=dict(base, builder_ready=True))
        self.assertEqual(
            build_ai_diagnostic_context(ready)["engine"]["health"]["health_state"],
            "WAITING_FOR_FIRST_SCAN",
        )

    def test_active_requires_fresh_symbol_trade_builder_and_scan(self):
        import time
        worker = Mock()
        worker.is_alive.return_value = True
        now = time.monotonic()
        app = SimpleNamespace(
            auto_trader_thread=worker,
            auto_health={"websocket_connected": True,
                         "last_selected_symbol_trade_at": now,
                         "builder_ready": True, "last_scan_at": now},
        )
        self.assertEqual(
            build_ai_diagnostic_context(app)["engine"]["health"]["health_state"],
            "ACTIVE",
        )

    def test_safety_gate_block_is_not_misreported_as_worker_crash(self):
        app = SimpleNamespace(
            auto_trader_thread=None,
            auto_health={"worker_phase": "blocked_by_safety",
                         "last_stream_error": "PaperRunnerGateError"},
        )
        health = build_ai_diagnostic_context(app)["engine"]["health"]
        self.assertEqual(health["health_state"], "BLOCKED_BY_SAFETY")
        self.assertEqual(health["last_stream_error"], "PaperRunnerGateError")

    def test_engine_health_report_exposes_actionable_stream_telemetry(self):
        from ai_diagnostics import build_engine_health_report_lines
        import time
        worker = Mock()
        worker.is_alive.return_value = True
        app = SimpleNamespace(
            auto_trader_thread=worker,
            auto_health={
                "worker_phase": "connected_waiting_for_trade",
                "websocket_connected": True,
                "retry_attempt": 2,
                "received_messages": 12,
                "valid_market_trades": 9,
                "selected_symbol_trade_count": 0,
                "connected_at": time.monotonic(),
                "last_stream_error": "",
            },
        )
        lines = build_engine_health_report_lines(app)
        rendered = "\\n".join(lines)
        self.assertIn("سلامت جریان معاملات: WAITING_FOR_SELECTED_SYMBOL_TRADE", rendered)
        self.assertIn("اتصال WebSocket: True", rendered)
        self.assertIn("تلاش اتصال مجدد: 2", rendered)
        self.assertIn("پیام‌های دریافتی: 12", rendered)
        self.assertIn("معاملات معتبر همه نمادها: 9", rendered)
        self.assertIn("معاملات معتبر نماد انتخابی: 0", rendered)
        self.assertIn("آخرین خطای جریان: ندارد", rendered)

    def test_connected_selected_trades_replace_stale_startup_phase(self):
        import time
        worker = Mock()
        worker.is_alive.return_value = True
        now = time.monotonic()
        base = {
            "worker_phase": "connected_waiting_for_trade",
            "websocket_connected": True,
            "last_selected_symbol_trade_at": now,
            "selected_symbol_trade_count": 300,
        }
        cases = (
            ({"candle_count": 3, "builder_ready": False},
             "collecting_selected_symbol_candles", "WARMING_UP"),
            ({"candle_count": 5, "builder_ready": False},
             "preliminary_monitoring", "WARMING_UP"),
            ({"candle_count": 23, "builder_ready": True},
             "analysis_ready_waiting_for_scan", "WAITING_FOR_FIRST_SCAN"),
        )
        for telemetry, expected_phase, expected_health in cases:
            with self.subTest(expected_phase=expected_phase):
                app = SimpleNamespace(
                    auto_trader_thread=worker,
                    auto_health=dict(base, **telemetry),
                )
                health = build_ai_diagnostic_context(app)["engine"]["health"]
                self.assertEqual(health["worker_phase"], expected_phase)
                self.assertEqual(health["health_state"], expected_health)

    def test_warmup_report_explains_remaining_candles_and_deferred_scan(self):
        import time
        from ai_diagnostics import build_engine_health_report_lines
        worker = Mock()
        worker.is_alive.return_value = True
        app = SimpleNamespace(
            auto_trader_thread=worker,
            auto_health={
                "worker_phase": "collecting_selected_symbol_candles",
                "websocket_connected": True,
                "last_selected_symbol_trade_at": time.monotonic(),
                "candle_count": 1,
                "analysis_required_candles": 23,
                "candle_interval_seconds": 60,
                "builder_ready": False,
                "last_scan_at": None,
            },
        )
        rendered = "\\n".join(build_engine_health_report_lines(app))
        self.assertIn("کندل‌های باقی‌مانده تا تحلیل: 22", rendered)
        self.assertIn("بازه هر کندل (ثانیه): 60", rendered)
        self.assertIn("هنوز اجرا نشده؛ در انتظار تکمیل کندل‌های معتبر", rendered)

    def test_history_provider_and_cache_status_are_reported_without_secrets(self):
        from ai_diagnostics import build_engine_health_report_lines
        worker = Mock()
        worker.is_alive.return_value = True
        app = SimpleNamespace(
            auto_trader_thread=worker,
            auto_health={
                "websocket_connected": True,
                "last_selected_symbol_trade_at": __import__("time").monotonic(),
                "builder_ready": False,
                "candle_history_provider_status": "NOT_DOCUMENTED_DISABLED",
                "candle_history_provider_error": "historical klines are not documented",
                "candle_cache_status": "cache missing",
                "candle_cache_file_exists": False,
                "candle_cache_restored_count": 0,
            },
        )
        rendered = "\\n".join(build_engine_health_report_lines(app))
        self.assertIn("وضعیت منبع تاریخچه کندل: NOT_DOCUMENTED_DISABLED", rendered)
        self.assertIn("علت نبود تاریخچه API: historical klines are not documented", rendered)
        self.assertIn("وضعیت کش کندل: cache missing", rendered)
        self.assertIn("وجود فایل کش کندل: False", rendered)
        self.assertIn("تعداد کندل بازیابی‌شده از کش: 0", rendered)

    def test_dead_worker_is_reported_even_if_socket_flag_is_true(self):
        worker = Mock()
        worker.is_alive.return_value = False
        app = SimpleNamespace(auto_trader_thread=worker,
                              auto_health={"websocket_connected": True})
        self.assertEqual(
            build_ai_diagnostic_context(app)["engine"]["health"]["health_state"],
            "WORKER_STOPPED",
        )

if __name__ == "__main__":
    unittest.main()
