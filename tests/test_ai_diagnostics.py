import unittest
from types import SimpleNamespace

from ai_diagnostics import build_ai_diagnostic_context


class AIDiagnosticContextTests(unittest.TestCase):
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
            last_market_snapshot={"bids": ["B" * 200] * 20},
        )
        context = build_ai_diagnostic_context(app)
        self.assertEqual(len(context["selected_symbol"]), 40)
        self.assertEqual(len(context["diagnostics"]["market_error"]), 500)
        self.assertEqual(len(context["market_snapshot"]["bids"]), 10)
        self.assertEqual(len(context["market_snapshot"]["bids"][0]), 120)


if __name__ == "__main__":
    unittest.main()
